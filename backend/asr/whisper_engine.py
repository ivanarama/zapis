"""Faster-whisper движок: ленивая загрузка модели, мультиязычная транскрипция."""

from __future__ import annotations

import gc
import logging
import os
from typing import Optional

from ..formats import format_result
from .base import (
    EngineStatus,
    HUGGINGFACE_HOST,
    SAMPLE_RATE,
    TranscribeResult,
    decode_audio_bytes,
    describe_download_error,
)

log = logging.getLogger("zapis.asr.whisper")

# Модели можно подменить из settings.json: asr.whisper.model
DEFAULT_MODEL = "small"

WHISPER_LANGUAGES = [
    "auto", "en", "ru", "es", "de", "fr", "it", "pt", "pl", "nl", "tr",
    "ja", "ko", "zh", "ar", "uk", "cs", "ro", "el", "sv", "fi", "no", "da",
    "hu", "id", "vi", "th", "he", "hi",
]


def _detect_device() -> str:
    """Есть ли GPU — надо спрашивать у CTranslate2, а не у torch.

    Whisper считает на CTranslate2, torch тут вообще не участвует. При этом в
    дистрибутиве torch собран без CUDA, так что torch.cuda.is_available()
    всегда False — и автоопределение через него навсегда запирало Whisper на
    CPU даже на машине с рабочей видеокартой.
    """
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # noqa: BLE001 — нет CUDA-рантайма, старый драйвер и т.п.
        log.info("CUDA-устройства не найдены, Whisper пойдёт на CPU", exc_info=True)
        return "cpu"


def _describe_cuda_error(exc: BaseException, device: str) -> Optional[str]:
    """Подсказка вместо «DLL load failed», когда GPU затребован, но не выходит."""
    if device != "cuda":
        return None
    return (
        "Не удалось запустить Whisper на видеокарте. Нужны NVIDIA-драйвер с "
        "поддержкой CUDA 12 и библиотека cuDNN 9; на не-NVIDIA GPU режим «cuda» "
        "не работает вовсе. Поставьте asr.whisper.device = \"cpu\" в settings.json, "
        f"чтобы вернуться на процессор. [{type(exc).__name__}: {str(exc)[:160]}]"
    )


class WhisperEngine:
    """ASR-движок на faster-whisper. Модель грузится лениво при первом
    transcribe() — это позволяет приложению стартовать без скачивания
    весов, если пользователь работает только с GigaAM."""

    name = "whisper"

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "auto",
        cpu_threads: int = 0,
    ):
        self._model_size = model_size
        self._device = device
        self._cpu_threads = cpu_threads
        self._model = None
        self._error: Optional[str] = None
        self._loading = False

    def supported_languages(self) -> list[str]:
        return WHISPER_LANGUAGES

    def get_status(self) -> EngineStatus:
        if self._error:
            return {"status": "error", "engine": self.name, "error": self._error}
        if self._model is not None:
            return {
                "status": "ready",
                "engine": self.name,
                "detail": f"faster-whisper {self._model_size}",
            }
        if self._loading:
            return {
                "status": "loading",
                "engine": self.name,
                "detail": f"faster-whisper {self._model_size}",
            }
        return {
            "status": "idle",
            "engine": self.name,
            "detail": f"faster-whisper {self._model_size} (модель будет загружена при первом запуске)",
        }

    def initialize(self) -> None:
        """Эта инициализация дорогая (скачивание весов), поэтому вызывается
        только при первой транскрипции, не при старте приложения."""
        if self._model is not None or self._error:
            return
        self._loading = True
        device = self._device  # на случай падения до автоопределения
        try:
            from faster_whisper import WhisperModel  # type: ignore

            device = self._device if self._device != "auto" else _detect_device()
            compute_type = "float16" if device == "cuda" else "int8"
            # CTranslate2 при cpu_threads=0 берёт 4 потока независимо от числа
            # ядер: на 12-ядерной машине это ~20% загрузки CPU и втрое более
            # долгая транскрибация. Считаем по числу ядер; 0 в настройках =
            # «авто», явное число — ручное ограничение.
            threads = self._cpu_threads if self._cpu_threads > 0 else (os.cpu_count() or 4)
            log.info(
                "Loading faster-whisper %s on %s (%s), cpu_threads=%d",
                self._model_size, device, compute_type, threads,
            )
            self._model = WhisperModel(
                self._model_size,
                device=device,
                compute_type=compute_type,
                cpu_threads=threads,
            )
            log.info("faster-whisper ready")
        except Exception as exc:
            log.exception("Failed to load faster-whisper")
            self._error = (
                describe_download_error(
                    exc, f"модель Whisper «{self._model_size}»", HUGGINGFACE_HOST
                )
                or _describe_cuda_error(exc, device)
                or str(exc)
            )
        finally:
            self._loading = False

    def _drop_model(self) -> None:
        """Отпускает загруженную модель и сразу собирает мусор.

        Без явного сбора старая модель доживает до ближайшей автоматической
        сборки, и в момент загрузки новой в памяти оказываются обе. На переходе
        small → large-v3 это лишние полтора гигабайта в пике — на машине с 8 ГБ
        разница между «перезагрузилось» и «не хватило памяти».
        """
        self._model = None
        gc.collect()

    def set_model_size(self, size: str) -> None:
        """Переключение размера модели — приведёт к перезагрузке при следующем
        вызове transcribe()."""
        if size == self._model_size:
            return
        self._model_size = size
        self._drop_model()
        self._error = None

    def set_cpu_threads(self, threads: int) -> None:
        """Как и set_model_size — параметр конструктора модели, поэтому смена
        сбрасывает уже загруженную модель."""
        if threads == self._cpu_threads:
            return
        self._cpu_threads = threads
        self._drop_model()
        self._error = None

    def transcribe(
        self,
        file_bytes: bytes,
        filename: str,
        language: str = "auto",
    ) -> TranscribeResult:
        if self._model is None and not self._error:
            self.initialize()
        if self._error:
            raise RuntimeError(f"Whisper не загружен: {self._error}")
        if self._model is None:
            raise RuntimeError("Whisper не инициализирован")

        ext = filename.rsplit(".", maxsplit=1)[-1] if "." in filename else "wav"
        audio = decode_audio_bytes(file_bytes, ext)

        lang_arg = None if language in (None, "", "auto") else language
        segments_iter, info = self._model.transcribe(
            audio,
            language=lang_arg,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )

        words: list[dict] = []
        for seg in segments_iter:
            seg_words = getattr(seg, "words", None) or []
            if seg_words:
                for w in seg_words:
                    text = (w.word or "").strip()
                    if not text:
                        continue
                    words.append({
                        "text": text,
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                    })
            else:
                text = (seg.text or "").strip()
                if not text:
                    continue
                words.append({
                    "text": text,
                    "start": round(float(seg.start), 3),
                    "end": round(float(seg.end), 3),
                })

        detected = getattr(info, "language", None) or language or "auto"
        return format_result(words, language=detected)
