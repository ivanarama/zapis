"""Расстановка ударений для синтеза (офлайн, ruaccent).

Silero сам угадывает ударения (put_accent), но на омографах и редких словах
ошибается постоянно («за́мок/замо́к», «бо́льшая/больша́я», имена). ruaccent —
отдельная модель расстановки ударений: возвращает текст с «+» после ударной
гласной — формат, который Silero понимает напрямую, — и заодно восстанавливает
«ё». Веса качаются с HuggingFace при первом использовании и кэшируются: тот же
принцип «скачать при первом запуске», что и у Silero/GigaAM/Whisper (build.ps1).

Память: модель грузим лениво и умеем выгружать (unload) — держать её
одновременно с фазой синтеза незачем, а на машине с 8 ГБ ОЗУ пик памяти важнее
(см. memory: dev-machine-ram). Pipeline расставляет ударения в фазе подготовки,
затем выгружает акцентайзер перед синтезом.

ruaccent — необязательная зависимость: при любом сбое импорта/загрузки/обработки
молча возвращаем исходный текст, чтобы озвучка не прерывалась (как делает
num2words в backend.tts.normalize). Тогда ударения по-прежнему расставит сам
Silero — просто менее точно.
"""

from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger("zavuk.tts.stress")

# Омограф-модель ruaccent. tiny бережёт ОЗУ; словарь (use_dictionary) покрывает
# подавляющее большинство слов, а модель нужна лишь для разрешения омографов —
# поэтому на 8 ГБ tiny по умолчанию, более крупные размеры (turbo3.1 и т.п.) —
# опция для машин помощнее. Значение прокидывается как есть и валидируется
# попыткой загрузки, чтобы не привязываться к набору размеров конкретной версии.
DEFAULT_MODEL_SIZE = "tiny"

# Разбиение на абзацы — то же, что у chunker: ударения расставляем поабзацно,
# чтобы (а) сохранить разделители \n\n, по которым chunker ставит паузы между
# абзацами, и (б) дать модели цельный контекст предложения для омографов.
_PARA_SPLIT = re.compile(r"\n\s*\n")


class Accentizer:
    """Обёртка над ruaccent. Потокобезопасная ленивая инициализация + выгрузка."""

    def __init__(self, model_size: str = DEFAULT_MODEL_SIZE):
        self.model_size = model_size
        self._engine = None
        self._failed = False
        self._lock = threading.Lock()

    def _ensure(self):
        """Грузит модель один раз. При сбое помечает _failed и больше не пробует."""
        if self._engine is not None or self._failed:
            return self._engine
        with self._lock:
            if self._engine is not None or self._failed:
                return self._engine
            try:
                from ruaccent import RUAccent

                from ..config import _app_dir

                # Веса кладём в постоянный кэш рядом с приложением. По умолчанию
                # ruaccent грузит их в каталог пакета, а во frozen-сборке это
                # эфемерный _MEIPASS — модель скачивалась бы при каждом запуске.
                workdir = _app_dir() / "cache" / "ruaccent"
                workdir.mkdir(parents=True, exist_ok=True)

                acc = RUAccent()
                acc.load(
                    omograph_model_size=self.model_size,
                    use_dictionary=True,
                    tiny_mode=False,
                    workdir=str(workdir),
                )
                self._engine = acc
                log.info("ruaccent загружен (omograph=%s, workdir=%s).", self.model_size, workdir)
            except Exception as e:  # noqa: BLE001 — фича необязательная, не валим синтез
                self._failed = True
                log.warning(
                    "ruaccent недоступен (%s) — ударения расставит сам Silero.", e
                )
            return self._engine

    def accentize(self, text: str) -> str:
        """Возвращает текст с «+» над ударными гласными. При сбое — исходный текст."""
        if not (text or "").strip():
            return text
        acc = self._ensure()
        if acc is None:
            return text
        out: list[str] = []
        for block in _PARA_SPLIT.split(text):
            if not block.strip():
                continue
            try:
                out.append(acc.process_all(block))
            except Exception as e:  # noqa: BLE001 — плохой абзац не должен рушить главу
                log.warning("Сбой расстановки ударений в абзаце: %s", e)
                out.append(block)
        return "\n\n".join(out)

    def unload(self) -> None:
        """Освобождает модель — вызывать после фазы подготовки, до синтеза."""
        with self._lock:
            self._engine = None


_accentizer: Accentizer | None = None
_lock = threading.Lock()


def get_accentizer(model_size: str = DEFAULT_MODEL_SIZE) -> Accentizer:
    """Синглтон акцентайзера. Пересоздаётся при смене размера модели."""
    global _accentizer
    with _lock:
        if _accentizer is None or _accentizer.model_size != model_size:
            _accentizer = Accentizer(model_size)
        return _accentizer
