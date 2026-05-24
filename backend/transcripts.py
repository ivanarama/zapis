"""Хранилище расшифровок: по одному JSON-файлу на расшифровку в каталоге
``transcripts/`` рядом с settings.json.

Позволяет разнести во времени транскрибацию и ИИ-обработку: результат ASR
сохраняется сразу, а сгенерированные ИИ-блоки дописываются позже.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("zapis.transcripts")

_lock = threading.Lock()
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _store_dir() -> Path:
    d = _app_dir() / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(tid: str) -> str:
    """Защита от path traversal: id должен быть простым токеном."""
    if not tid or not _ID_RE.match(tid):
        raise ValueError("Некорректный идентификатор расшифровки")
    return tid


def _path(tid: str) -> Path:
    return _store_dir() / f"{_safe_id(tid)}.json"


def _write(record: dict[str, Any]) -> None:
    path = _path(record["id"])
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _duration(result: dict[str, Any]) -> float:
    segments = result.get("segments") or []
    if not segments:
        return 0.0
    try:
        return float(segments[-1].get("end") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def list_transcripts() -> list[dict[str, Any]]:
    """Лёгкий список (без сегментов и тела ИИ-блоков), отсортирован по дате."""
    out: list[dict[str, Any]] = []
    for f in _store_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Не удалось прочитать %s", f.name)
            continue
        out.append({
            "id": data.get("id", f.stem),
            "name": data.get("name", f.stem),
            "source": data.get("source", ""),
            "engine": data.get("engine", ""),
            "language": data.get("language", ""),
            "created_at": data.get("created_at", 0),
            "updated_at": data.get("updated_at", 0),
            "duration": _duration(data.get("result") or {}),
            "ai_count": len(data.get("ai_blocks") or []),
        })
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out


def get_transcript(tid: str) -> Optional[dict[str, Any]]:
    path = _path(tid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("Повреждённый файл расшифровки %s", path.name)
        return None


def create_transcript(
    *,
    name: str,
    source: str,
    engine: str,
    language: str,
    result: dict[str, Any],
    ai_blocks: Optional[list] = None,
) -> dict[str, Any]:
    now = time.time()
    record = {
        "id": uuid.uuid4().hex[:16],
        "name": name or "Без названия",
        "source": source,
        "engine": engine,
        "language": language,
        "created_at": now,
        "updated_at": now,
        "result": result,
        "ai_blocks": ai_blocks or [],
    }
    with _lock:
        _write(record)
    return record


def update_transcript(tid: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Частичное обновление: name / ai_blocks / result."""
    with _lock:
        record = get_transcript(tid)
        if record is None:
            return None
        for key in ("name", "ai_blocks", "result"):
            if key in patch and patch[key] is not None:
                record[key] = patch[key]
        record["updated_at"] = time.time()
        _write(record)
        return record


def delete_transcript(tid: str) -> bool:
    path = _path(tid)
    with _lock:
        if not path.exists():
            return False
        path.unlink()
        return True
