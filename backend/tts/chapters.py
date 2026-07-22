"""Разбивка книги на главы по заголовкам.

Заголовки ищем построчно по настраиваемому regex (по умолчанию — «Глава N»,
«Часть N», «Chapter N» и Markdown-заголовки). Если ничего не найдено — вся
книга одной главой.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Строка-заголовок: «Глава 3», «ГЛАВА III», «Часть первая», «Chapter 5»,
# либо Markdown «# …». Применяется с IGNORECASE к обрезанной строке.
DEFAULT_CHAPTER_PATTERN = r"^\s*(?:глава|часть|chapter|part)\b.*$|^\s*#{1,6}\s+\S.*$"

# Заголовок не должен быть слишком длинным (иначе это обычное предложение,
# начавшееся со слова «Часть …»).
_MAX_TITLE_LEN = 80


@dataclass
class Chapter:
    title: str
    text: str


def _is_heading(line: str, pat: re.Pattern) -> bool:
    s = line.strip()
    if not s or len(s) > _MAX_TITLE_LEN:
        return False
    return bool(pat.match(s))


def _clean_title(line: str) -> str:
    return line.strip().lstrip("#").strip() or "Глава"


def split_chapters(text: str, pattern: str | None = None) -> list[Chapter]:
    """Делит текст на главы. Возвращает минимум одну главу с непустым телом."""
    pat = re.compile(pattern or DEFAULT_CHAPTER_PATTERN, re.IGNORECASE)

    chapters: list[Chapter] = []
    cur_title: str | None = None
    cur_lines: list[str] = []
    had_heading = False

    def flush():
        body = "\n".join(cur_lines).strip()
        if body:
            # Текст до первого заголовка — это предисловие/вступление («Начало»);
            # если заголовков не было вовсе — вся книга одной главой («Книга»).
            title = cur_title or ("Начало" if had_heading else "Книга")
            chapters.append(Chapter(title=title, text=body))

    for line in text.split("\n"):
        if _is_heading(line, pat):
            had_heading = True
            flush()
            cur_title = _clean_title(line)
            cur_lines = []
        else:
            cur_lines.append(line)
    flush()

    if not chapters:
        return [Chapter(title="Книга", text=text.strip())]
    return chapters
