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
# Устройство, заданное отдельно для конкретного движка (asr.<движок>.device).
# Пусто = наследовать общий asr.device.
_device_overrides: dict[str, str] = {}


def _resolve_device(name: str) -> str:
    """Устройство для конкретного движка: своя настройка важнее общей."""
    return _device_overrides.get(name) or _device


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
            _engines[target] = _create(target, device=_resolve_device(target))
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


def set_device(device: str, overrides: Optional[dict[str, Optional[str]]] = None) -> bool:
    """Задать устройство: общее и, при необходимости, отдельное для движков.

    Возвращает True, если устройство действительно поменялось.

    Устройство фиксируется в момент создания движка, поэтому уже созданные
    сбрасываем — иначе настройка не подействует до перезапуска. Но сбрасывать
    их на каждый вызов нельзя: настройки сохраняются целиком при правке любого
    поля, и пользователь, поменявший промпт, терял бы прогретую модель и ждал
    её повторной загрузки. Поэтому при неизменившемся устройстве не делаем
    ничего.
    """
    global _device, _device_overrides
    normalized = {k: v for k, v in (overrides or {}).items() if v}
    with _lock:
        if device == _device and normalized == _device_overrides:
            return False
        _device = device
        _device_overrides = normalized
        _engines.clear()
        return True


def available_engines() -> list[str]:
    return ["gigaam", "whisper"]


def available_languages(engine: Optional[str] = None) -> list[str]:
    eng = get_engine(engine)
    return eng.supported_languages()
