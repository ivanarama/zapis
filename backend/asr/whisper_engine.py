"""Faster-whisper движок: ленивая загрузка модели, мультиязычная транскрипция."""

from __future__ import annotations

import logging
from typing import Optional

from ..formats import format_result
from .base import EngineStatus, SAMPLE_RATE, TranscribeResult, decode_audio_bytes

log = logging.getLogger("zapis.asr.whisper")

# Модели можно подменить из settings.json: asr.whisper.model
DEFAULT_MODEL = "small"

WHISPER_LANGUAGES = [
    "auto", "en", "ru", "es", "de", "fr", "it", "pt", "pl", "nl", "tr",
    "ja", "ko", "zh", "ar", "uk", "cs", "ro", "el", "sv", "fi", "no", "da",
    "hu", "id", "vi", "th", "he", "hi",
]


class WhisperEngine:
    """ASR-движок на faster-whisper. Модель грузится лениво при первом
    transcribe() — это позволяет приложению стартовать без скачивания
    весов, если пользователь работает только с GigaAM."""

    name = "whisper"

    def __init__(self, model_size: str = DEFAULT_MODEL, device: str = "auto"):
        self._model_size = model_size
        self._device = device
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
        try:
            from faster_whisper import WhisperModel  # type: ignore
            import torch

            device = (
                self._device
                if self._device != "auto"
                else ("cuda" if torch.cuda.is_available() else "cpu")
            )
            compute_type = "float16" if device == "cuda" else "int8"
            log.info(
                "Loading faster-whisper %s on %s (%s)",
                self._model_size, device, compute_type,
            )
            self._model = WhisperModel(
                self._model_size, device=device, compute_type=compute_type,
            )
            log.info("faster-whisper ready")
        except Exception as exc:
            log.exception("Failed to load faster-whisper")
            self._error = str(exc)
        finally:
            self._loading = False

    def set_model_size(self, size: str) -> None:
        """Переключение размера модели — приведёт к перезагрузке при следующем
        вызове transcribe()."""
        if size == self._model_size:
            return
        self._model_size = size
        self._model = None
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
