"""Экспорт аудио через PyAV (libav) — без внешнего ffmpeg.exe.

WAV пишем stdlib-модулем wave; mp3/m4b кодируем PyAV (тот же `av`, что уже
используется в backend.asr.base для декодирования). Главы по умолчанию
отдаём отдельными файлами — это самый совместимый «аудиокнижный» формат и не
требует глав-атомов в контейнере.

Запись — потоковая: `AudioStreamWriter` принимает аудио кусками и сразу
кодирует/мьюксит каждый, не накапливая весь звук в памяти. Это держит пиковую
память постоянной независимо от длины книги (раньше длинная книга падала с
[Errno 12] Cannot allocate memory при попытке закодировать весь массив разом).
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

# Кодируем блоками (~10 с аудио за кадр), а НЕ одним гигантским кадром на всю
# книгу: иначе libav пытается выделить буфер на весь текст и падает с
# «[Errno 12] Cannot allocate memory» на длинных книгах.
_BLOCK_SECONDS = 10


def file_ext(audio_format: str) -> str:
    return _FORMATS.get(audio_format, _FORMATS["mp3"])[2]


def sanitize_filename(name: str, fallback: str = "audiobook") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or fallback


def _to_pcm16(audio: np.ndarray) -> np.ndarray:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")


class _WavWriter:
    """Потоковая запись WAV: дописываем кадры по мере поступления."""

    def __init__(self, path: str | Path, sample_rate: int):
        self._w = wave.open(str(path), "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(sample_rate)

    def write(self, audio: np.ndarray) -> None:
        if audio is not None and audio.size:
            self._w.writeframes(_to_pcm16(audio).tobytes())

    def close(self) -> None:
        self._w.close()


class _AvWriter:
    """Потоковая запись через PyAV: ресэмпл → FIFO → кодер → мьюкс, блоками."""

    def __init__(
        self,
        path: str | Path,
        sample_rate: int,
        *,
        codec: str,
        container_format: str | None,
        bitrate: int,
        metadata: dict | None,
    ):
        import av

        self._av = av
        self._sr = sample_rate
        self._block = sample_rate * _BLOCK_SECONDS
        self._container = av.open(str(path), mode="w", format=container_format)
        self._stream = self._container.add_stream(codec, rate=sample_rate)
        if bitrate:
            self._stream.bit_rate = int(bitrate)
        if metadata:
            self._container.metadata.update(
                {k: str(v) for k, v in metadata.items() if v not in (None, "")}
            )

        # Целевой формат сэмплов кодера (aac→fltp, mp3→s16p/fltp). Если PyAV
        # ещё не раскрыл формат — используем fltp (его принимают оба кодера).
        target_fmt = "fltp"
        try:
            if self._stream.format is not None and self._stream.format.name:
                target_fmt = self._stream.format.name
        except Exception:  # noqa: BLE001
            pass

        self._resampler = av.AudioResampler(format=target_fmt, layout="mono", rate=sample_rate)
        self._fifo = av.AudioFifo()
        # AAC требует кадры ровно frame_size; mp3 терпит дефолт.
        self._frame_size = self._stream.codec_context.frame_size or 1024

    def _drain(self, final: bool = False) -> None:
        # Выгребаем накопленные сэмплы кадрами frame_size. При final дочитываем
        # неполный остаток, чтобы не потерять хвост.
        while self._fifo.samples >= self._frame_size:
            frame = self._fifo.read(self._frame_size)
            if frame is None:
                break
            for packet in self._stream.encode(frame):
                self._container.mux(packet)
        if final:
            tail = self._fifo.read()
            if tail is not None:
                for packet in self._stream.encode(tail):
                    self._container.mux(packet)

    def write(self, audio: np.ndarray) -> None:
        if audio is None or not audio.size:
            return
        audio = np.ascontiguousarray(np.clip(audio, -1.0, 1.0), dtype=np.float32)
        for start in range(0, audio.shape[0], self._block):
            chunk = np.ascontiguousarray(audio[start : start + self._block].reshape(1, -1))
            src = self._av.AudioFrame.from_ndarray(chunk, format="fltp", layout="mono")
            src.sample_rate = self._sr
            src.pts = None
            for resampled in self._resampler.resample(src):
                self._fifo.write(resampled)
            self._drain()

    def close(self) -> None:
        try:
            # Досушиваем ресэмплер (если буферизует при смене формата) и FIFO.
            try:
                for resampled in self._resampler.resample(None):
                    self._fifo.write(resampled)
            except (ValueError, TypeError):
                pass  # старые PyAV не умеют flush ресэмплера — нечего досушивать
            self._drain(final=True)
            for packet in self._stream.encode(None):  # flush кодера
                self._container.mux(packet)
        finally:
            self._container.close()


class AudioStreamWriter:
    """Инкрементальная запись аудио в файл нужного формата (wav/mp3/m4a/m4b).

    Принимает float32 mono в [-1, 1] кусками произвольной длины и кодирует их
    на лету. В single-file режиме один writer держат открытым на всю книгу,
    скармливая ему главы по очереди.
    """

    def __init__(
        self,
        path: str | Path,
        sample_rate: int,
        audio_format: str = "mp3",
        bitrate: int = 128000,
        metadata: dict | None = None,
    ):
        codec, container_format, _ = _FORMATS.get(audio_format, _FORMATS["mp3"])
        if codec is None:
            self._backend: _WavWriter | _AvWriter = _WavWriter(path, sample_rate)
        else:
            self._backend = _AvWriter(
                path,
                sample_rate,
                codec=codec,
                container_format=container_format,
                bitrate=bitrate,
                metadata=metadata,
            )

    def write(self, audio: np.ndarray) -> None:
        self._backend.write(audio)

    def close(self) -> None:
        self._backend.close()

    def __enter__(self) -> "AudioStreamWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def write_audio(
    path: str | Path,
    audio: np.ndarray,
    sample_rate: int,
    audio_format: str = "mp3",
    bitrate: int = 128000,
    metadata: dict | None = None,
) -> None:
    """Пишет готовый массив в файл (wav/mp3/m4a/m4b) одним вызовом.

    Для длинных книг pipeline пишет потоково через AudioStreamWriter; эта
    функция — удобная обёртка для коротких/цельных массивов и тестов.
    """
    with AudioStreamWriter(path, sample_rate, audio_format, bitrate, metadata) as w:
        w.write(audio)
