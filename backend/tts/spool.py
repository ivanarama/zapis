"""Временный дисковый буфер PCM для двухпроходной озвучки.

Чтобы не держать весь звук главы в ОЗУ (на длинной книге это гигабайты —
вылетает [Errno 12] / процесс убивает ОС по нехватке памяти), синтезированные
фрагменты пишем во временный файл сырым float32, попутно считая пиковую
амплитуду. На втором проходе читаем обратно блоками, домножаем на коэффициент
нормализации громкости и отдаём кодеру. Пиковая память при этом не зависит от
длины книги — она равна одному блоку.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator

import numpy as np

log = logging.getLogger("zavuk.tts.spool")


class PcmSpool:
    """Буфер float32 mono на диске с подсчётом пика и поблочным чтением."""

    def __init__(self, block_samples: int):
        fd, self._path = tempfile.mkstemp(prefix="zvuk_tts_", suffix=".f32")
        self._f = os.fdopen(fd, "wb")
        self._peak = 0.0
        self._samples = 0
        self._block = max(1, int(block_samples))

    def write(self, audio: np.ndarray | None) -> None:
        """Дописывает фрагмент в буфер и обновляет пик."""
        if audio is None or not audio.size:
            return
        a = np.ascontiguousarray(audio, dtype=np.float32)
        m = float(np.max(np.abs(a)))
        if m > self._peak:
            self._peak = m
        self._f.write(a.tobytes())
        self._samples += int(a.shape[0])

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def samples(self) -> int:
        return self._samples

    def gain(self, target_peak: float = 0.95) -> float:
        """Коэффициент мягкой нормализации пика к target_peak (1.0, если тишина)."""
        return target_peak / self._peak if self._peak > 0 else 1.0

    def finish(self) -> None:
        """Закрывает запись. Вызывать перед blocks()."""
        if self._f is not None:
            self._f.close()
            self._f = None

    def blocks(self, gain: float = 1.0) -> Iterator[np.ndarray]:
        """Читает буфер блоками float32, домножая на gain. Вызывать после finish()."""
        g = np.float32(gain)
        chunk_bytes = self._block * 4  # float32 = 4 байта
        with open(self._path, "rb") as r:
            while True:
                buf = r.read(chunk_bytes)
                if not buf:
                    break
                yield np.frombuffer(buf, dtype="<f4") * g

    def close(self) -> None:
        """Закрывает запись и удаляет временный файл."""
        self.finish()
        try:
            os.unlink(self._path)
        except OSError as e:  # noqa: BLE001
            log.warning("Не удалось удалить временный файл %s: %s", self._path, e)

    def __enter__(self) -> "PcmSpool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
