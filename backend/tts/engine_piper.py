"""Piper TTS-движок: офлайн-синтез русской речи на CPU (ONNX).

Альтернатива Silero для уровня «качество»: русские голоса (ruslan, dmitri,
irina, denis) звучат живее и интонационно богаче Silero. Лицензия Piper — MIT
(в отличие от XTTS), фонемизация — через espeak-ng, чьи данные лежат ВНУТРИ
пакета piper, поэтому собираются в exe как есть (см. build.ps1 collect_all).

Голос (.onnx + .onnx.json) качается с HuggingFace (rhasspy/piper-voices) при
первом использовании в постоянный кэш рядом с приложением — тот же принцип
«скачать при первом запуске», что у Silero/ruaccent. Лёгкий по памяти (медиум-
голос ~60 МБ + onnxruntime) — спокойно работает на 8 ГБ.

Piper НЕ понимает «+»-разметку ударений (своя фонемизация через espeak), поэтому
accepts_accent_marks = False — pipeline не прогоняет текст через ruaccent для
Piper (это бы только мусорило фонемы).
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger("zavuk.tts.engine_piper")

# Русские голоса rhasspy/piper-voices: medium-качество, 22050 Гц, одноголосые.
PIPER_RU_VOICES = [
    "ru_RU-ruslan-medium",
    "ru_RU-dmitri-medium",
    "ru_RU-irina-medium",
    "ru_RU-denis-medium",
]
DEFAULT_SPEAKER = "ru_RU-ruslan-medium"
DEFAULT_SAMPLE_RATE = 22050  # medium-голоса; фактическое значение берём из config


class PiperEngine:
    """Обёртка над piper. Голоса грузятся лениво и кэшируются по speaker."""

    name = "piper"
    accepts_accent_marks = False

    def __init__(self, device: str = "cpu"):
        self._device_pref = device
        self._voices: dict = {}  # speaker -> PiperVoice
        self._status: str = "idle"
        self._error: str | None = None
        self._lock = threading.Lock()

    # ---- lifecycle ----

    def initialize(self) -> None:
        """Лёгкая проверка готовности. Голоса грузятся лениво в _get_voice."""
        if self._status == "ready":
            return
        with self._lock:
            if self._status == "ready":
                return
            try:
                import piper  # noqa: F401

                self._status = "ready"
                log.info("Piper доступен.")
            except Exception as e:  # noqa: BLE001
                self._status = "error"
                self._error = str(e)
                log.exception("Не удалось импортировать piper")
                raise

    def get_status(self) -> dict:
        return {
            "status": self._status,
            "engine": self.name,
            "speakers": PIPER_RU_VOICES,
            "error": self._error or "",
        }

    def list_speakers(self) -> list:
        return list(PIPER_RU_VOICES)

    # ---- voices ----

    def _cache_dir(self):
        from ..config import _app_dir

        d = _app_dir() / "cache" / "piper"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _get_voice(self, speaker: str):
        """Возвращает загруженный PiperVoice, при необходимости скачивая модель."""
        if speaker not in PIPER_RU_VOICES:
            speaker = DEFAULT_SPEAKER
        v = self._voices.get(speaker)
        if v is not None:
            return v
        with self._lock:
            v = self._voices.get(speaker)
            if v is not None:
                return v
            from piper import PiperVoice
            from piper.download_voices import download_voice

            cache = self._cache_dir()
            onnx = cache / f"{speaker}.onnx"
            cfg = cache / f"{speaker}.onnx.json"
            if not (onnx.exists() and cfg.exists()):
                log.info("Скачиваю голос Piper %s…", speaker)
                download_voice(speaker, cache)
            # espeak_data_dir не задаём — piper берёт встроенные данные из пакета.
            voice = PiperVoice.load(
                str(onnx), config_path=str(cfg), use_cuda=(self._device_pref == "cuda")
            )
            self._voices[speaker] = voice
            log.info("Piper голос %s готов (sr=%d).", speaker, voice.config.sample_rate)
            return voice

    def resolve_sample_rate(self, speaker: str, requested: int) -> int:
        """Частоту диктует модель голоса (medium = 22050); requested игнорируем."""
        return int(self._get_voice(speaker).config.sample_rate)

    # ---- synthesis ----

    def synth(
        self,
        text: str,
        speaker: str = DEFAULT_SPEAKER,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        put_accent: bool = True,  # noqa: ARG002 — для совместимости интерфейса с Silero
        put_yo: bool = True,  # noqa: ARG002
        length_scale: float | None = None,
        **kwargs,
    ) -> np.ndarray:
        """Синтезирует фрагмент → float32 mono PCM в [-1, 1] на частоте голоса."""
        clean = (text or "").strip()
        if not clean:
            return np.zeros(0, dtype=np.float32)

        voice = self._get_voice(speaker)
        from piper.config import SynthesisConfig

        # normalize_audio=False: громкость выравниваем своей поглавной нормализацией
        # пика (spool.gain), иначе Piper нормирует каждое предложение отдельно и
        # громкость «гуляет» по книге.
        syn = SynthesisConfig(length_scale=length_scale, normalize_audio=False)
        parts: list[np.ndarray] = []
        for chunk in voice.synthesize(clean, syn_config=syn):
            arr = chunk.audio_int16_array
            if arr is not None and len(arr):
                parts.append(arr)
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return (np.concatenate(parts).astype(np.float32) / 32768.0).astype(np.float32)


_engine: PiperEngine | None = None
_lock = threading.Lock()


def get_engine(device: str = "cpu") -> PiperEngine:
    """Синглтон Piper-движка."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = PiperEngine(device=device)
        return _engine
