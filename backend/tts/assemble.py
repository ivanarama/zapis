"""Склейка PCM-фрагментов, паузы и выравнивание громкости.

Работаем с float32 mono в [-1, 1] на общей частоте дискретизации.
"""

from __future__ import annotations

import numpy as np


def silence(ms: int, sample_rate: int) -> np.ndarray:
    n = max(0, int(sample_rate * ms / 1000))
    return np.zeros(n, dtype=np.float32)


def concat(parts: list[np.ndarray]) -> np.ndarray:
    real = [p for p in parts if p is not None and p.size]
    if not real:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(real).astype(np.float32)


def peak_normalize(audio: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    """Приводит пик к target_peak (мягкая нормализация громкости)."""
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak <= 0:
        return audio
    return (audio * (target_peak / peak)).astype(np.float32)
