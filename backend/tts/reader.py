"""Чтение исходного текста книги из .txt.

Русские .txt часто приходят в cp1251 — поэтому детектим кодировку перебором,
без внешних зависимостей (charset-normalizer в проект не тянем).
"""

from __future__ import annotations

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "koi8-r", "cp866", "latin-1")


def decode_txt(data: bytes) -> str:
    """Декодирует байты .txt, перебирая типичные для русского кодировки."""
    for enc in _ENCODINGS:
        try:
            return _normalize_newlines(data.decode(enc))
        except UnicodeDecodeError:
            continue
    # latin-1 не бросает UnicodeDecodeError, но на всякий случай — с заменой.
    return _normalize_newlines(data.decode("utf-8", errors="replace"))


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
