"""Управление settings.json: чтение, валидация, кэш по mtime, сохранение.

Адаптировано из C:/Projects/localChat/backend/config.py — оставлено только то,
что нужно Zapis (без шифрования секретов и без remote-settings)."""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from .schema import Settings

log = logging.getLogger("zapis.config")

_settings: Settings | None = None
_mtime: float = 0.0
_lock = threading.Lock()


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _settings_path() -> Path:
    return _app_dir() / "settings.json"


def _load_raw_dict(path: Path) -> tuple[dict[str, Any], float]:
    try:
        mtime = path.stat().st_mtime
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        return (data if isinstance(data, dict) else {}), mtime
    except OSError:
        return {}, -1.0
    except json.JSONDecodeError:
        log.warning("Некорректный JSON в %s — использую дефолты.", path)
        try:
            return {}, path.stat().st_mtime
        except OSError:
            return {}, -1.0


def load_settings(force: bool = False) -> Settings:
    global _settings, _mtime
    path = _settings_path()
    try:
        disk_mtime = path.stat().st_mtime
    except OSError:
        disk_mtime = -1.0

    if not force and _settings is not None and disk_mtime == _mtime:
        return _settings

    with _lock:
        if not force and _settings is not None and disk_mtime == _mtime:
            return _settings
        data, file_mtime = _load_raw_dict(path)
        try:
            _settings = Settings.model_validate(data)
        except Exception:
            log.warning("Ошибка валидации settings.json — использую дефолты.", exc_info=True)
            _settings = Settings()
        _mtime = file_mtime
    return _settings


def save_settings(settings: Settings) -> None:
    global _settings, _mtime
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        data = settings.model_dump(mode="json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _settings = settings
        try:
            _mtime = path.stat().st_mtime
        except OSError:
            _mtime = -1.0


def update_settings(patch: dict[str, Any]) -> Settings:
    """Частичное обновление: deep-merge с текущими настройками + сохранение."""
    current = load_settings().model_dump(mode="json")
    merged = _deep_merge(current, patch)
    new_settings = Settings.model_validate(merged)
    save_settings(new_settings)
    return new_settings


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base) if isinstance(base, dict) else {}
    if not isinstance(overlay, dict):
        return out
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> Settings:
    return load_settings()
