"""Фабрика ASR-движков. Хранит синглтоны движков, создаёт по запросу."""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .base import Transcriber

log = logging.getLogger("zapis.asr.factory")

_lock = threading.Lock()
_engines: dict[str, Transcriber] = {}
_active: str = "gigaam"
_device: str = "auto"


def _create(name: str, device: str = "auto") -> Transcriber:
    if name == "gigaam":
        from .gigaam_engine import GigaamEngine
        return GigaamEngine(version="v3", device=device)
    if name == "whisper":
        from .whisper_engine import WhisperEngine
        return WhisperEngine(device=device)
    raise ValueError(f"Неизвестный движок ASR: {name}")


def get_engine(name: Optional[str] = None) -> Transcriber:
    """Получить (создать при необходимости) движок по имени.

    Создание дешёвое — фактическая загрузка модели идёт в initialize()."""
    target = name or _active
    with _lock:
        if target not in _engines:
            _engines[target] = _create(target, device=_device)
        return _engines[target]


def get_active_engine() -> Transcriber:
    return get_engine(_active)


def set_active_engine(name: str) -> Transcriber:
    global _active
    if name not in available_engines():
        raise ValueError(f"Неизвестный движок ASR: {name}")
    with _lock:
        _active = name
    eng = get_engine(name)
    return eng


def set_device(device: str) -> None:
    global _device
    _device = device


def available_engines() -> list[str]:
    return ["gigaam", "whisper"]


def available_languages(engine: Optional[str] = None) -> list[str]:
    eng = get_engine(engine)
    return eng.supported_languages()
