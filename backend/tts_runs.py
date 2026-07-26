"""Хранилище озвучек: по одному JSON на запуск в ``tts_runs/`` рядом с settings.json.

Зеркало backend/transcripts.py. Каждая озвучка = запись с метаданными
(id, title, author, engine, voice, created_at, output_dir, files). Сами аудиофайлы
лежат в audiobooks/<title>_<shortid>/ (отдаются через /api/tts/audio); запись
хранит только метаданные и путь к папке.

ensure_imported() при первом списке подхватывает папки audiobooks/* без записи
(старые/внешние озвучки) — чтобы история была полной с первого запуска.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("zapis.tts_runs")

_lock = threading.Lock()
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
AUDIO_EXTS = {".mp3", ".m4b", ".m4a", ".wav", ".ogg", ".opus"}


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _store_dir() -> Path:
    d = _app_dir() / "tts_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audiobooks_dir() -> Path:
    return _app_dir() / "audiobooks"


def _safe_id(tid: str) -> str:
    if not tid or not _ID_RE.match(tid):
        raise ValueError("Некорректный идентификатор озвучки")
    return tid


def _path(tid: str) -> Path:
    return _store_dir() / f"{_safe_id(tid)}.json"


def _write(record: dict[str, Any]) -> None:
    _path(record["id"]).write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _scan_audio(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.suffix.lower() in AUDIO_EXTS)


def ensure_imported() -> None:
    """Подхватывает папки audiobooks/* без записи в индекс (старые озвучки)."""
    ab = _audiobooks_dir()
    if not ab.is_dir():
        return
    known: set[str] = set()
    for f in _store_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("output_dir"):
                known.add(str(Path(data["output_dir"]).resolve()))
        except (OSError, json.JSONDecodeError):
            continue
    with _lock:
        for d in ab.iterdir():
            if not d.is_dir():
                continue
            key = str(d.resolve())
            if key in known:
                continue
            files = _scan_audio(d)
            if not files:
                continue  # пустая папка — не озвучка
            record = {
                "id": uuid.uuid4().hex[:16],
                "title": d.name,
                "author": "",
                "engine": "",
                "voice": "",
                "created_at": d.stat().st_mtime,
                "output_dir": str(d),
                "files": files,
            }
            _write(record)
            known.add(key)
            log.info("Импортирована озвучка в историю: %s", d.name)


def list_runs() -> list[dict[str, Any]]:
    """Лёгкий список (без содержимого файлов), отсортирован по дате (новые сверху)."""
    ensure_imported()
    out: list[dict[str, Any]] = []
    for f in _store_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Не удалось прочитать %s", f.name)
            continue
        out.append({
            "id": data.get("id", f.stem),
            "title": data.get("title", f.stem),
            "engine": data.get("engine", ""),
            "voice": data.get("voice", ""),
            "author": data.get("author", ""),
            "created_at": data.get("created_at", 0),
            "files_count": len(data.get("files") or []),
        })
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out


def get_run(tid: str) -> Optional[dict[str, Any]]:
    p = _path(tid)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("Повреждённый файл озвучки %s", p.name)
        return None
    # Синхронизируем список файлов с диска (могли добавиться/исчезнуть).
    data["files"] = _scan_audio(Path(data["output_dir"])) if data.get("output_dir") else []
    return data


def create_run(
    *,
    title: str,
    author: str,
    engine: str,
    voice: str,
    output_dir: str,
    files: list[str],
) -> dict[str, Any]:
    now = time.time()
    record = {
        "id": uuid.uuid4().hex[:16],
        "title": title or "Без названия",
        "author": author or "",
        "engine": engine or "",
        "voice": voice or "",
        "created_at": now,
        "output_dir": str(output_dir),
        "files": list(files or []),
    }
    with _lock:
        _write(record)
    return record


def delete_run(tid: str) -> bool:
    p = _path(tid)
    with _lock:
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        # Удаляем аудиопапку, но только если она под audiobooks/ (защита от path-traversal).
        od = data.get("output_dir")
        if od:
            d = Path(od)
            ab = _audiobooks_dir().resolve()
            try:
                dres = d.resolve()
                if dres != ab and ab in dres.parents and dres.is_dir():
                    shutil.rmtree(dres, ignore_errors=True)
            except Exception:  # noqa: BLE001
                log.warning("Не удалось удалить папку озвучки %s", od)
        p.unlink()
        return True
