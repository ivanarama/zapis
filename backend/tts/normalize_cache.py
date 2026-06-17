"""Дисковый кэш LLM-нормализации текста для озвучки.

Один и тот же фрагмент при тех же системном промпте и наборе LLM-моделей не
гоняется через модель повторно — в том числе между сеансами. Это ускоряет
повторную озвучку той же книги и экономит токены.

Ключ кэша = sha256(signature + текст), где signature включает промпт и
сигнатуру LLM-профилей. Смена промпта или модели → новый ключ → кэш
инвалидируется автоматически. Кэшируем ТОЛЬКО успешные ответы модели, прошедшие
проверку качества: rule-based-откат (из-за сетевой ошибки/обрыва) не кэшируем,
чтобы при следующем запуске можно было получить полноценный результат.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..config import _app_dir

log = logging.getLogger("zavuk.tts.normcache")


def _cache_dir() -> Path:
    d = _app_dir() / "cache" / "tts_normalize"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(text: str, signature: str) -> str:
    h = hashlib.sha256()
    h.update(signature.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def get(text: str, signature: str) -> str | None:
    """Возвращает кэшированный результат или None, если его нет/не прочитать."""
    try:
        return (_cache_dir() / f"{_key(text, signature)}.txt").read_text(encoding="utf-8")
    except OSError:
        return None


def put(text: str, signature: str, value: str) -> None:
    """Сохраняет результат. Ошибки записи не фатальны — просто без кэша."""
    try:
        (_cache_dir() / f"{_key(text, signature)}.txt").write_text(value, encoding="utf-8")
    except OSError as e:  # noqa: BLE001
        log.warning("Не удалось записать кэш нормализации: %s", e)
