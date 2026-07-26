"""Microsoft Edge neural TTS (edge-tts): бесплатные русские голоса без ключа.

Использует публичный endpoint «Читать вслух» Microsoft Edge через пакет edge-tts —
без API-ключа и без регистрации. Русские нейронные голоса (ru-RU-DmitryNeural,
ru-RU-SvetlanaNeural) заметно естественнее Piper/Silero. Это облако (нужен
интернет), но без учётных данных.

Возвращает MP3 -> float32 PCM (24 кГц). Async-API edge-tts запускаем синхронно
(pipeline зовёт synth через asyncio.to_thread — в потоке нет event loop).

РОБАСТНОСТЬ под нагрузкой книги. Публичный endpoint нестабилен: при серии запросов
он транзиентно отдаёт «No audio was received» или рвёт соединение. Поэтому:
  - темп (PACE) между запросами — снижает rate-limit;
  - ATTEMPTS попыток на фрагмент с экспоненциальным backoff;
  - провал фрагмента НЕ рвёт книгу: вставляем короткую тишину и идём дальше;
  - но FAIL_THRESHOLD провалов подряд = endpoint лёг -> CloudTtsError (аборт,
    иначе получили бы книгу из одной тишины). Счётчик сбрасывается на успехе и
    после паузы (новый запуск книги).

Оговорка: endpoint неофициальный (реверс-инжиниринг), без SLA — только личное
использование, не для коммерции.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import numpy as np

from .engine_cloud_base import decode_audio, split_text
from .errors import CloudTtsError

log = logging.getLogger("zavuk.tts.engine_edge")

# Русские нейронные голоса Edge. Каталог broad — можно расширить (edge-tts --list-voices).
EDGE_VOICES = ["ru-RU-DmitryNeural", "ru-RU-SvetlanaNeural"]


class EdgeEngine:
    name = "edge"
    accepts_accent_marks = False  # своя просодия
    sample_rate = 24000  # ru-RU neural voices edge-tts = 24 кГц
    text_limit = 2000
    voices = EDGE_VOICES
    default_voice = "ru-RU-DmitryNeural"

    # Робастность к нестабильному публичному endpoint:
    PACE = 0.3              # пауза между запросами (с), снижает rate-limit
    ATTEMPTS = 5            # попыток на фрагмент: backoff 0.5/1/2/4/8 c
    FAIL_THRESHOLD = 5      # N провалов подряд -> считаем endpoint упавшим (аборт)
    SESSION_RESET = 30.0    # пауза > N c -> новый запуск, счётчик провалов сбрасываем

    def __init__(self):
        self._consecutive_failures = 0
        self._last_fail_monotonic = 0.0
        self._lock = threading.Lock()

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

    def _on_chunk_failure(self, reason: str) -> np.ndarray:
        """Фрагмент не удался. Транзиентно -> ~1 c тишины (книга продолжится);
        FAIL_THRESHOLD подряд -> CloudTtsError (чтобы не получить книгу из тишины)."""
        now = time.monotonic()
        with self._lock:
            # Новый запуск книги (большая пауза) — даём свежий бюджет неудач,
            # иначе после прошлого аборта повтор тут же бы оборвался.
            if self._last_fail_monotonic and now - self._last_fail_monotonic > self.SESSION_RESET:
                self._consecutive_failures = 0
            self._consecutive_failures += 1
            self._last_fail_monotonic = now
            n = self._consecutive_failures
        log.warning("edge-tts: пропуск фрагмента (%d подряд): %s", n, reason)
        if n >= self.FAIL_THRESHOLD:
            raise CloudTtsError(
                f"edge-tts: {n} фрагментов подряд не synthesizingлись ({reason}). "
                "Endpoint недоступен — озвучка прервана, чтобы не получить книгу из тишины."
            )
        # Не фатально: короткая тишина на месте провала, книга продолжается.
        return np.zeros(self.sample_rate, dtype=np.float32)  # ~1 c тишины

    def synth(self, text, speaker=None, sample_rate=None, **opts) -> np.ndarray:
        clean = (text or "").strip()
        if not clean:
            return np.zeros(0, dtype=np.float32)
        voice = speaker if speaker in self.voices else self.default_voice

        async def _one(piece: str) -> bytes:
            import edge_tts

            await asyncio.sleep(self.PACE)  # мягкий темп между запросами
            last = "нет audio-чанков"
            for attempt in range(self.ATTEMPTS):
                try:
                    parts: list[bytes] = []
                    comm = edge_tts.Communicate(piece, voice)
                    async for chunk in comm.stream():
                        if chunk.get("type") == "audio":
                            parts.append(chunk.get("data", b""))
                    if parts:
                        return b"".join(parts)
                    last = "нет audio-чанков"
                except Exception as e:  # noqa: BLE001
                    last = str(e)
                await asyncio.sleep(0.5 * (2 ** attempt))  # 0.5/1/2/4/8 c
            raise CloudTtsError(
                f"edge-tts: фрагмент не synthesizingн за {self.ATTEMPTS} попыток ({last})"
            )

        async def _all() -> list[bytes]:
            return [await _one(p) for p in split_text(clean, self.text_limit)]

        try:
            mp3_chunks = asyncio.run(asyncio.wait_for(_all(), timeout=180))
        except CloudTtsError as e:
            return self._on_chunk_failure(str(e))
        except Exception as e:  # noqa: BLE001 — таймаут/сеть
            return self._on_chunk_failure(f"нет сети/endpoint/таймаут: {e}")

        decoded = [decode_audio(c, "mp3") for c in mp3_chunks if c]
        if not decoded:
            return self._on_chunk_failure("пустой аудио-ответ")

        with self._lock:  # успех — сбрасываем счётчик провалов
            self._consecutive_failures = 0
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
