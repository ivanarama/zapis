"""Silero TTS-движок: офлайн-синтез русской речи на CPU.

Модель грузится через torch.hub (snakers4/silero-models) один раз и кэшируется
в TORCH_HOME при первом запуске — по тому же принципу «скачать при первом
использовании», что и веса GigaAM/Whisper (см. build.ps1).
"""

from __future__ import annotations

import logging
import threading
from typing import Literal, TypedDict

import numpy as np

log = logging.getLogger("zavuk.tts.engine")

# Голоса модели v4_ru. random исключён — для книги нужен стабильный голос.
SPEAKERS_V4_RU = ["aidar", "baya", "kseniya", "xenia", "eugene"]
SAMPLE_RATES = [8000, 24000, 48000]
DEFAULT_SPEAKER = "baya"
DEFAULT_SAMPLE_RATE = 48000

# Silero не синтезирует кусок длиннее ~1000 символов за вызов.
MAX_CHARS_PER_CALL = 1000


class EngineStatus(TypedDict, total=False):
    status: Literal["idle", "loading", "ready", "error"]
    engine: str
    speakers: list
    detail: str
    error: str


class SileroEngine:
    """Обёртка над silero_tts. Потокобезопасная ленивая инициализация."""

    name = "silero"

    def __init__(self, version: str = "v4_ru", device: str = "cpu"):
        self.version = version
        # Silero отлично работает на CPU; "auto"/"cuda" сводим к cpu, если нет GPU.
        self._device_pref = device
        self._model = None
        self._status: str = "idle"
        self._error: str | None = None
        self._lock = threading.Lock()

    # ---- lifecycle ----

    def initialize(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            self._status = "loading"
            self._error = None
            try:
                import torch

                device = torch.device(
                    "cuda" if (self._device_pref == "cuda" and torch.cuda.is_available()) else "cpu"
                )
                log.info("Загрузка Silero %s на %s…", self.version, device)
                model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-models",
                    model="silero_tts",
                    language="ru",
                    speaker=self.version,
                    trust_repo=True,
                )
                model.to(device)
                self._model = model
                self._device = device
                self._status = "ready"
                log.info("Silero готов.")
            except Exception as e:  # noqa: BLE001
                self._status = "error"
                self._error = str(e)
                log.exception("Не удалось загрузить Silero")
                raise

    def get_status(self) -> EngineStatus:
        return {
            "status": self._status,  # type: ignore[typeddict-item]
            "engine": self.name,
            "speakers": SPEAKERS_V4_RU,
            "error": self._error or "",
        }

    # ---- synthesis ----

    def synth(
        self,
        text: str,
        speaker: str = DEFAULT_SPEAKER,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        put_accent: bool = True,
        put_yo: bool = True,
    ) -> np.ndarray:
        """Синтезирует один фрагмент → float32 mono PCM в [-1, 1].

        Бросает ValueError на пустой/непроизносимый текст — вызывающий код
        (pipeline) ловит и подставляет тишину, чтобы книга не прерывалась.
        """
        if self._model is None:
            self.initialize()
        if speaker not in SPEAKERS_V4_RU:
            speaker = DEFAULT_SPEAKER
        if sample_rate not in SAMPLE_RATES:
            sample_rate = DEFAULT_SAMPLE_RATE

        clean = (text or "").strip()
        if not clean:
            return np.zeros(0, dtype=np.float32)

        audio = self._model.apply_tts(
            text=clean,
            speaker=speaker,
            sample_rate=sample_rate,
            put_accent=put_accent,
            put_yo=put_yo,
        )
        return audio.detach().cpu().numpy().astype(np.float32)


_engine: SileroEngine | None = None
_lock = threading.Lock()


def get_engine(version: str = "v4_ru", device: str = "cpu") -> SileroEngine:
    """Синглтон движка. Пересоздаётся при смене версии модели."""
    global _engine
    with _lock:
        if _engine is None or _engine.version != version:
            _engine = SileroEngine(version=version, device=device)
        return _engine
