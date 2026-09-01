"""Диаризация: кто из говорящих звучит в каждый момент записи (sherpa-onnx).

Стек ровно тот же, что у ``pyannote/speaker-diarization-3.1``: сегментация
``pyannote-segmentation-3.0`` + эмбеддинги ``WeSpeaker ResNet34-LM``. Отличие в
источнике: берём ONNX-зеркала с GitHub Releases проекта sherpa-onnx, а не
оригиналы с HuggingFace. Оригиналы лежат за гейтом (нужен аккаунт, принятие
условий и токен) — на машине подписчика они бы просто не скачались, а весь
смысл фичи в том, что она работает у всех, кому раздали приложение.

Считает на onnxruntime внутри sherpa-onnx: torch тут не участвует вовсе, режим
всегда CPU (колесо sherpa-onnx с PyPI собрано без CUDA). Модели (~34 МБ)
качаются при первом включении диаризации в ``cache/diarization`` рядом с
приложением — тем же принципом «скачать по требованию», что веса ruaccent и
голоса Piper.

Память: сами модели крошечные, пик держит сигнал (час записи в float32 — это
~230 МБ). Поэтому вызывающий код не должен держать рядом второй такой же
буфер: сначала транскрибация, потом диаризация, а не одновременно
(см. memory: dev-machine-ram).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tarfile
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional, TypedDict

import numpy as np

from ..config import _app_dir
from .base import (
    EngineStatus,
    GITHUB_HOST,
    SAMPLE_RATE,
    describe_download_error,
)

log = logging.getLogger("zapis.asr.diarize")

_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

# Сегментация (кто когда говорит) — pyannote-segmentation-3.0 в ONNX, MIT.
# Архив ~7 МБ; внутри нужна только model.onnx, остальное — int8-вариант и
# тестовые wav, которые на диске пользователя ни к чему.
SEGMENTATION_URL = (
    f"{_RELEASES}/speaker-segmentation-models/"
    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)

# ВНИМАНИЕ: «recongition» — опечатка в имени тега у апстрима, а не у нас.
# Исправлять нельзя: с правильным написанием ссылка отдаёт 404.
EMBEDDING_URL = f"{_RELEASES}/speaker-recongition-models/{{model}}"

# Пара по умолчанию = ровно то, на чём работает pyannote/speaker-diarization-3.1.
DEFAULT_EMBEDDING_MODEL = "wespeaker_en_voxceleb_resnet34_LM.onnx"

# Шаг окна сегментации. У pyannote — 0.1 (окно 10 с двигается по секунде, то
# есть каждый участок считается десять раз). На замерах 0.3 давал ту же
# разметку втрое быстрее, а 0.5 уже ломал кластеризацию — отсюда умолчание.
DEFAULT_WINDOW_SHIFT = 0.3

# Модели эмбеддингов для выпадающего списка в настройках. Русскоязычных среди
# них нет (публичных вообще нет), но эмбеддинг описывает тембр голоса, а не
# язык — на русской речи они работают, разница между ними невелика.
EMBEDDING_MODELS: dict[str, str] = {
    "wespeaker_en_voxceleb_resnet34_LM.onnx": "WeSpeaker ResNet34-LM — как в pyannote 3.1 (27 МБ)",
    "wespeaker_en_voxceleb_CAM++.onnx": "WeSpeaker CAM++ — быстрее, чуть слабее (29 МБ)",
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx": "3D-Speaker CAM++ zh+en (28 МБ)",
    "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx": "3D-Speaker ERes2NetV2 — тяжелее (71 МБ)",
}

INSTALL_HINT = (
    "Пакет sherpa-onnx не установлен, диаризация недоступна. "
    "Установите его командой  pip install sherpa-onnx==1.13.7  и перезапустите приложение."
)


class Turn(TypedDict):
    """Реплика: интервал времени и номер говорящего (нумерация с нуля)."""

    start: float
    end: float
    speaker: int


def _models_dir() -> Path:
    return _app_dir() / "cache" / "diarization"


def _download(url: str, dest: Path, what: str) -> None:
    """Скачивает файл во временный .part и переименовывает по завершении.

    Без .part оборванная загрузка оставляет на диске обрезанный .onnx, который
    при следующем запуске выглядит как готовая модель и падает уже внутри
    onnxruntime — с сообщением, по которому причину не восстановить.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".part")
    log.info("Скачиваю %s: %s", what, url)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f, length=1 << 20)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    log.info("%s готова: %s (%.1f МБ)", what, dest.name, dest.stat().st_size / 1e6)


def _extract_segmentation(archive: Path, dest: Path) -> None:
    """Достаёт из tar.bz2 единственный нужный файл — model.onnx.

    Читаем член архива потоком и пишем сами, а не через extract(): так путь
    назначения задаём мы, и вредоносные пути внутри архива («../») значения не
    имеют.
    """
    with tarfile.open(archive, "r:bz2") as tf:
        member = next(
            (m for m in tf.getmembers() if m.isfile() and Path(m.name).name == "model.onnx"),
            None,
        )
        if member is None:
            raise RuntimeError("В архиве модели сегментации нет model.onnx")
        src = tf.extractfile(member)
        if src is None:
            raise RuntimeError("Не удалось прочитать model.onnx из архива")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / (dest.name + ".part")
        with src, open(tmp, "wb") as f:
            shutil.copyfileobj(src, f, length=1 << 20)
        tmp.replace(dest)


def renumber_by_first_appearance(turns: list[Turn]) -> list[Turn]:
    """Перенумеровывает говорящих по порядку первого появления.

    Кластеризация раздаёт номера произвольно, и «Спикер 3», заговоривший
    первым, читается как ошибка. Ожидание у пользователя простое: первый
    заговоривший — первый в списке.
    """
    mapping: dict[int, int] = {}
    out: list[Turn] = []
    for t in sorted(turns, key=lambda x: (x["start"], x["end"])):
        if t["speaker"] not in mapping:
            mapping[t["speaker"]] = len(mapping)
        out.append({"start": t["start"], "end": t["end"], "speaker": mapping[t["speaker"]]})
    return out


class Diarizer:
    """Обёртка над sherpa-onnx: ленивая загрузка, потокобезопасная инициализация.

    Устойчива к повторным вызовам ``initialize()`` — как и ASR-движки.
    """

    name = "diarization"

    def __init__(
        self,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        num_threads: int = 0,
        window_shift_ratio: float = DEFAULT_WINDOW_SHIFT,
        min_duration_on: float = 0.3,
        min_duration_off: float = 0.5,
    ):
        self._embedding_model = embedding_model or DEFAULT_EMBEDDING_MODEL
        self._num_threads = num_threads
        self._window_shift_ratio = window_shift_ratio
        self._min_duration_on = min_duration_on
        self._min_duration_off = min_duration_off
        self._sd = None
        self._config = None
        self._error: Optional[str] = None
        self._loading = False
        self._lock = threading.RLock()

    # ---------- статус и настройки ----------

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    def get_status(self) -> EngineStatus:
        if self._error:
            return {"status": "error", "engine": self.name, "error": self._error}
        if self._sd is not None:
            return {
                "status": "ready",
                "engine": self.name,
                "detail": f"pyannote-segmentation-3.0 + {self._embedding_model}",
            }
        if self._loading:
            return {"status": "loading", "engine": self.name, "detail": "загрузка моделей…"}
        if not self.models_ready():
            return {
                "status": "idle",
                "engine": self.name,
                "detail": "модели (~34 МБ) будут скачаны при первом запуске",
            }
        return {"status": "idle", "engine": self.name, "detail": "модели скачаны"}

    def set_embedding_model(self, name: str) -> None:
        """Смена модели эмбеддингов пересоздаёт распознаватель при следующем
        вызове: модель фиксируется в конструкторе sherpa-onnx (как размер
        модели у Whisper)."""
        name = name or DEFAULT_EMBEDDING_MODEL
        if name == self._embedding_model:
            return
        with self._lock:
            self._embedding_model = name
            self._sd = None
            self._config = None
            self._error = None

    def set_num_threads(self, threads: int) -> None:
        if threads == self._num_threads:
            return
        with self._lock:
            self._num_threads = threads
            self._sd = None
            self._config = None
            self._error = None

    def reset_error(self) -> None:
        """Сбрасывает залипшую ошибку перед повторной попыткой.

        initialize() при выставленном _error молча выходит — без сброса кнопка
        «Скачать модели» после первой неудачи (не было сети) не делала бы
        ничего до перезапуска приложения. Пишем без блокировки: присваивание
        атомарно, а вызов приходит из event loop, где ждать нельзя.
        """
        self._error = None

    def set_window_shift_ratio(self, ratio: float) -> None:
        """Шаг окна тоже задаётся при создании модели — меняем так же, через
        пересоздание при следующем обращении."""
        if not ratio or ratio == self._window_shift_ratio:
            return
        with self._lock:
            self._window_shift_ratio = ratio
            self._sd = None
            self._config = None
            self._error = None

    # ---------- модели ----------

    def _segmentation_path(self) -> Path:
        return _models_dir() / "pyannote-segmentation-3.0.onnx"

    def _embedding_path(self) -> Path:
        return _models_dir() / self._embedding_model

    def models_ready(self) -> bool:
        return self._segmentation_path().exists() and self._embedding_path().exists()

    def download_models(self) -> None:
        """Скачивает недостающие модели.

        Вызывается и из initialize(), и отдельной кнопкой в UI — чтобы 34 МБ
        качались при включении галочки, а не посреди транскрибации.
        """
        seg = self._segmentation_path()
        if not seg.exists():
            archive = _models_dir() / "segmentation.tar.bz2"
            _download(SEGMENTATION_URL, archive, "модель сегментации речи")
            try:
                _extract_segmentation(archive, seg)
            finally:
                archive.unlink(missing_ok=True)

        emb = self._embedding_path()
        if not emb.exists():
            _download(
                EMBEDDING_URL.format(model=self._embedding_model),
                emb,
                f"модель голосовых эмбеддингов «{self._embedding_model}»",
            )

    def initialize(self) -> None:
        if self._sd is not None or self._error:
            return
        with self._lock:
            if self._sd is not None or self._error:
                return
            self._loading = True
            try:
                try:
                    import sherpa_onnx
                except ImportError:
                    log.warning("sherpa-onnx не установлен — диаризация недоступна")
                    self._error = INSTALL_HINT
                    return

                # Хост в тексте ошибки называем явно: у моделей диаризации он
                # третий по счёту (GitHub), и «не качается» без указания хоста
                # диагностировать невозможно.
                try:
                    self.download_models()
                except Exception as exc:
                    self._error = (
                        describe_download_error(exc, "модели диаризации", GITHUB_HOST)
                        or str(exc)
                    )
                    log.exception("Не удалось скачать модели диаризации")
                    return

                threads = self._num_threads if self._num_threads > 0 else (os.cpu_count() or 4)
                log.info(
                    "Загружаю диаризацию (эмбеддинги %s, потоков %d, шаг окна %.2f)",
                    self._embedding_model, threads, self._window_shift_ratio,
                )
                config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
                    segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                            model=str(self._segmentation_path()),
                            window_shift_ratio=self._window_shift_ratio,
                        ),
                        num_threads=threads,
                    ),
                    embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                        model=str(self._embedding_path()),
                        num_threads=threads,
                    ),
                    clustering=sherpa_onnx.FastClusteringConfig(
                        num_clusters=-1, threshold=0.5,
                    ),
                    min_duration_on=self._min_duration_on,
                    min_duration_off=self._min_duration_off,
                )
                if not config.validate():
                    # Чаще всего — обрезанный при обрыве связи файл модели.
                    raise RuntimeError(
                        "sherpa-onnx отверг конфигурацию диаризации: файлы моделей "
                        f"повреждены или недоступны ({_models_dir()}). "
                        "Удалите этот каталог и включите диаризацию снова."
                    )
                self._config = config
                self._sd = sherpa_onnx.OfflineSpeakerDiarization(config)
                log.info("Диаризация готова")
            except Exception as exc:  # noqa: BLE001 — сообщение уходит в UI
                log.exception("Не удалось инициализировать диаризацию")
                self._error = self._error or str(exc)
            finally:
                self._loading = False

    def unload(self) -> None:
        """Освобождает ONNX-сессии. Модели небольшие, но на 8 ГБ каждая сотня
        мегабайт на счету, а между транскрибациями диаризатор не нужен.

        Если разметка идёт прямо сейчас, молча выходим: модели заняты, а ждать
        нельзя — вызов приходит из обработчика сохранения настроек, то есть из
        event loop, и ожидание подвесило бы весь интерфейс на минуты.
        """
        if not self._lock.acquire(blocking=False):
            log.info("Диаризация занята — выгрузку моделей пропускаю")
            return
        try:
            self._sd = None
        finally:
            self._lock.release()

    # ---------- собственно диаризация ----------

    def diarize(
        self,
        pcm: np.ndarray,
        num_speakers: int = 0,
        threshold: float = 0.5,
        progress: Optional[Callable[[float], None]] = None,
    ) -> list[Turn]:
        """Размечает, кто когда говорит.

        pcm — моно float32 16 кГц (то, что отдаёт decode_audio_bytes).
        num_speakers <= 0 — число говорящих неизвестно, кластеризуем по порогу.
        """
        if self._sd is None and not self._error:
            self.initialize()
        if self._error:
            raise RuntimeError(self._error)
        if self._sd is None:
            raise RuntimeError("Диаризация ещё загружается")

        audio = np.ascontiguousarray(pcm, dtype=np.float32)
        if audio.ndim != 1:
            audio = audio.reshape(-1)
        if audio.size < SAMPLE_RATE:  # меньше секунды — делить некого
            return []

        expected = int(self._sd.sample_rate)
        if expected != SAMPLE_RATE:
            raise RuntimeError(
                f"Модель диаризации ждёт {expected} Гц, а декодер отдаёт {SAMPLE_RATE} Гц"
            )

        import sherpa_onnx

        with self._lock:
            # Число говорящих задаётся на каждый запуск, поэтому кластеризацию
            # обновляем перед каждым проходом. Модели при этом не
            # перезагружаются — set_config меняет только конфигурацию.
            self._config.clustering = sherpa_onnx.FastClusteringConfig(
                num_clusters=int(num_speakers) if num_speakers and num_speakers > 0 else -1,
                threshold=float(threshold),
            )
            self._sd.set_config(self._config)

            if progress is not None:
                def _cb(done: int, total: int) -> int:
                    try:
                        progress(done / total if total else 0.0)
                    except Exception:  # noqa: BLE001 — прогресс не должен ронять счёт
                        pass
                    return 0

                result = self._sd.process(audio, callback=_cb)
            else:
                result = self._sd.process(audio)

        turns: list[Turn] = [
            {
                "start": round(float(r.start), 3),
                "end": round(float(r.end), 3),
                "speaker": int(r.speaker),
            }
            for r in result.sort_by_start_time()
        ]
        turns = renumber_by_first_appearance(turns)
        log.info(
            "Диаризация: реплик %d, говорящих %d",
            len(turns), len({t["speaker"] for t in turns}),
        )
        return turns


# ---------- синглтон ----------

_lock = threading.Lock()
_instance: Optional[Diarizer] = None


def get_diarizer(
    embedding_model: Optional[str] = None,
    num_threads: Optional[int] = None,
    window_shift_ratio: Optional[float] = None,
) -> Diarizer:
    """Единственный экземпляр на процесс. Создание дешёвое — модели грузятся
    в initialize()."""
    global _instance
    with _lock:
        created = _instance is None
        if created:
            _instance = Diarizer(
                embedding_model=embedding_model or DEFAULT_EMBEDDING_MODEL,
                num_threads=num_threads or 0,
                window_shift_ratio=window_shift_ratio or DEFAULT_WINDOW_SHIFT,
            )
    inst = _instance
    if not created:
        if embedding_model:
            inst.set_embedding_model(embedding_model)
        if num_threads is not None:
            inst.set_num_threads(num_threads)
        if window_shift_ratio is not None:
            inst.set_window_shift_ratio(window_shift_ratio)
    return inst


def unload() -> None:
    """Выгружает модели, если они были загружены.

    Нужно на выключении диаризации в настройках: держать ONNX-сессии, которыми
    больше не пользуются, на машине с 8 ГБ незачем (см. memory: dev-machine-ram).
    """
    if _instance is not None:
        _instance.unload()


def is_available() -> bool:
    """Установлен ли sherpa-onnx.

    Нужно, чтобы UI не предлагал галочку, которая заведомо не сработает
    (сборка без пакета, запуск из исходников без установки зависимостей).
    """
    if "sherpa_onnx" in sys.modules:
        return True
    try:
        import importlib.util

        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False
