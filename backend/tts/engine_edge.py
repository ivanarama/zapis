"""Microsoft Edge neural TTS (edge-tts): бесплатные русские голоса без ключа.

Использует публичный endpoint «Читать вслух» Microsoft Edge через пакет edge-tts —
без API-ключа и без регистрации. Русские нейронные голоса (ru-RU-DmitryNeural,
ru-RU-SvetlanaNeural) заметно естественнее Piper/Silero. Это облако (нужен
интернет), но без учётных данных — самый простой вариант «бесплатно и сразу».

Возвращает MP3 → декодим в float32 PCM (24 кГц). Async-API edge-tts запускаем
синхронно: pipeline зовёт synth через asyncio.to_thread, в потоке нет event loop,
поэтому asyncio.run безопасен. Сбои сети/endpoint оборачиваем в CloudTtsError,
чтобы pipeline прерывал книгу с понятной ошибкой, а не подменял тишину.

Оговорка: endpoint неофициальный (реверс-инжиниринг), без SLA — только для личного
использования, не для коммерции.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import numpy as np

from .engine_cloud_base import decode_audio, split_text
from .errors import CloudTtsError

log = logging.getLogger("zavuk.tts.engine_edge")

# Русские нейронные голоса Edge. Каталог broad — можно расширить (см. edge-tts --list-voices).
EDGE_VOICES = ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"]


class EdgeEngine:
    name = "edge"
    accepts_accent_marks = False  # своя просодия
    sample_rate = 24000  # ru-RU neural voices edge-tts = 24 кГц
    text_limit = 2000
    voices = EDGE_VOICES
    default_voice = "ru-RU-DmitryNeural"

    def initialize(self) -> None:
        import edge_tts  # noqa: F401  — проверка доступности пакета

    def get_status(self) -> dict:
        try:
            import edge_tts  # noqa: F401

            status, err = "ready", ""
        except ImportError as e:
            status, err = "error", f"edge-tts не установлен: {e}"
        return {
            "status": status,
            "engine": self.name,
            "speakers": list(self.voices),
            "hifi": [],
            "error": err,
        }

    def resolve_sample_rate(self, speaker, requested) -> int:
        return self.sample_rate

    def list_speakers(self) -> list[str]:
        return list(self.voices)

    def synth(self, text, speaker=None, sample_rate=None, **opts) -> np.ndarray:
        clean = (text or "").strip()
        if not clean:
            return np.zeros(0, dtype=np.float32)
        voice = speaker if speaker in self.voices else self.default_voice

        async def _one(piece: str) -> bytes:
            import edge_tts

            # endpoint неофициальный и иногда транзиентно отдаёт «No audio» —
            # для длинной книги это критично, поэтому ретраим с backoff.
            last = "пустой ответ (нет audio-чанков)"
            for attempt in range(3):
                try:
                    parts: list[bytes] = []
                    comm = edge_tts.Communicate(piece, voice)
                    async for chunk in comm.stream():
                        if chunk.get("type") == "audio":
                            parts.append(chunk.get("data", b""))
                    if parts:
                        return b"".join(parts)
                    last = "пустой ответ (нет audio-чанков)"
                except Exception as e:  # noqa: BLE001
                    last = str(e)
                await asyncio.sleep(0.5 * (attempt + 1))  # backoff: 0.5/1.0/1.5 c
            raise CloudTtsError(
                f"edge-tts: не удалось синтезировать фрагмент после 3 попыток ({last})"
            )

        async def _all() -> list[bytes]:
            return [await _one(p) for p in split_text(clean, self.text_limit)]

        try:
            mp3_chunks = asyncio.run(asyncio.wait_for(_all(), timeout=60))
        except CloudTtsError:
            raise
        except Exception as e:  # noqa: BLE001 — таймаут/сеть/endpoint
            raise CloudTtsError(f"edge-tts недоступен (нет сети/endpoint): {e}") from e

        decoded = [decode_audio(c, "mp3") for c in mp3_chunks if c]
        if not decoded:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(decoded).astype(np.float32)


_engine: EdgeEngine | None = None
_lock = threading.Lock()


def get_engine(device: str = "cpu") -> EdgeEngine:
    """Синглтон edge-движка. device игнорируется (синтез в облаке)."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = EdgeEngine()
        return _engine
