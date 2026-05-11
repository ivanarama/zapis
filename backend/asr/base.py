"""Базовые типы и интерфейс ASR-движка."""

from __future__ import annotations

import os
import tempfile
from typing import Literal, Protocol, TypedDict, runtime_checkable

import numpy as np

SAMPLE_RATE = 16_000


def decode_audio_bytes(data: bytes, ext: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Декодирует аудио/видео в float32 mono PCM нужной частоты через pyav.

    pyav приходит транзитивной зависимостью faster-whisper, поэтому мы
    избавлены от внешнего ffmpeg.exe в PATH (раньше оба движка падали с
    FileNotFoundError на машинах без ffmpeg).
    """
    import av  # локальный импорт, чтобы не тянуть pyav при импорте пакета

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(data)
        tmp_path = f.name

    try:
        with av.open(tmp_path) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise ValueError("В файле не найден аудио-поток")

            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=sample_rate,
            )
            chunks: list[np.ndarray] = []
            for frame in container.decode(stream):
                for r in resampler.resample(frame):
                    chunks.append(r.to_ndarray())
            for r in resampler.resample(None):  # flush
                chunks.append(r.to_ndarray())

        if not chunks:
            return np.zeros(0, dtype=np.float32)
        pcm = np.concatenate(chunks, axis=1).reshape(-1)
        return pcm.astype(np.float32) / 32768.0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class EngineStatus(TypedDict, total=False):
    status: Literal["idle", "loading", "ready", "error", "needs_install"]
    engine: str
    detail: str
    error: str
    install_hint: str


class TranscribeResult(TypedDict):
    text: str
    segments: list
    language: str


@runtime_checkable
class Transcriber(Protocol):
    """Протокол ASR-движка. Реализации должны быть устойчивы к повторным
    вызовам initialize() — повторный init не должен переинициализировать
    уже загруженную модель."""

    name: str

    def initialize(self) -> None: ...

    def get_status(self) -> EngineStatus: ...

    def transcribe(
        self,
        file_bytes: bytes,
        filename: str,
        language: str = "ru",
    ) -> TranscribeResult: ...

    def supported_languages(self) -> list[str]: ...
