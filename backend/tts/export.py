"""Экспорт аудио через PyAV (libav) — без внешнего ffmpeg.exe.

WAV пишем stdlib-модулем wave; mp3/m4b кодируем PyAV (тот же `av`, что уже
используется в backend.asr.base для декодирования). Главы по умолчанию
отдаём отдельными файлами — это самый совместимый «аудиокнижный» формат и не
требует глав-атомов в контейнере.
"""

from __future__ import annotations

import logging
import re
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("zavuk.tts.export")

# format -> (codec, container_format, extension)
_FORMATS = {
    "wav": (None, None, "wav"),
    "mp3": ("mp3", None, "mp3"),
    "m4a": ("aac", "mp4", "m4a"),
    "m4b": ("aac", "mp4", "m4b"),
}


def file_ext(audio_format: str) -> str:
    return _FORMATS.get(audio_format, _FORMATS["mp3"])[2]


def sanitize_filename(name: str, fallback: str = "audiobook") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or fallback


def _to_pcm16(audio: np.ndarray) -> np.ndarray:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")


def write_wav(path: str | Path, audio: np.ndarray, sample_rate: int) -> None:
    pcm = _to_pcm16(audio)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def _encode_av(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    *,
    codec: str,
    container_format: str | None,
    bitrate: int,
    metadata: dict | None,
) -> None:
    import av

    audio = np.ascontiguousarray(np.clip(audio, -1.0, 1.0), dtype=np.float32)
    container = av.open(str(path), mode="w", format=container_format)
    try:
        stream = container.add_stream(codec, rate=sample_rate)
        if bitrate:
            stream.bit_rate = int(bitrate)
        if metadata:
            container.metadata.update(
                {k: str(v) for k, v in metadata.items() if v not in (None, "")}
            )

        # Целевой формат сэмплов кодера (aac→fltp, mp3→s16p/fltp). Если PyAV
        # ещё не раскрыл формат — используем fltp (его принимают оба кодера).
        target_fmt = "fltp"
        try:
            if stream.format is not None and stream.format.name:
                target_fmt = stream.format.name
        except Exception:  # noqa: BLE001
            pass

        resampler = av.AudioResampler(format=target_fmt, layout="mono", rate=sample_rate)
        fifo = av.AudioFifo()

        src = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="fltp", layout="mono")
        src.sample_rate = sample_rate
        src.pts = None
        for resampled in resampler.resample(src):
            fifo.write(resampled)

        # AAC требует кадры ровно frame_size; mp3 терпит дефолт. Последний
        # (неполный) кадр читаем отдельно.
        frame_size = stream.codec_context.frame_size or 1024
        while True:
            frame = fifo.read(frame_size)
            if frame is None:
                break
            for packet in stream.encode(frame):
                container.mux(packet)
        tail = fifo.read()
        if tail is not None:
            for packet in stream.encode(tail):
                container.mux(packet)
        for packet in stream.encode(None):  # flush
            container.mux(packet)
    finally:
        container.close()


def write_audio(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    audio_format: str = "mp3",
    bitrate: int = 128000,
    metadata: dict | None = None,
) -> None:
    """Пишет аудио в нужном формате (wav/mp3/m4a/m4b)."""
    codec, container_format, _ = _FORMATS.get(audio_format, _FORMATS["mp3"])
    if codec is None:
        write_wav(path, audio, sample_rate)
        return
    _encode_av(
        path,
        audio,
        sample_rate,
        codec=codec,
        container_format=container_format,
        bitrate=bitrate,
        metadata=metadata,
    )
