"""Нарезка текста на фрагменты для Silero.

Silero не синтезирует кусок длиннее ~1000 символов за вызов, а резать
посреди предложения нельзя (ломается интонация). Поэтому:
  1) делим главу на абзацы (двойной перенос строки) — чтобы расставить
     паузы между абзацами;
  2) каждый абзац делим на предложения (razdel) и жадно упаковываем в
     фрагменты ≤ max_chars, не разрывая предложения.

Возвращаем список абзацев, каждый — список фрагментов. Pipeline по этой
структуре расставляет паузы: короткую между фрагментами, длиннее — между
абзацами.
"""

from __future__ import annotations

import re

try:
    from razdel import sentenize

    def _sentences(text: str) -> list[str]:
        return [s.text for s in sentenize(text)]

except Exception:  # noqa: BLE001 — fallback без razdel
    def _sentences(text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?…])\s+", text)
        return [p for p in parts if p.strip()]


DEFAULT_MAX_CHARS = 800

_PARA_SPLIT = re.compile(r"\n\s*\n")


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Режет слишком длинное предложение по запятым, затем по словам."""
    if len(sentence) <= max_chars:
        return [sentence]
    pieces: list[str] = []
    buf = ""
    for part in re.split(r"(?<=[,;:—])\s+", sentence):
        if buf and len(buf) + 1 + len(part) > max_chars:
            pieces.append(buf)
            buf = part
        else:
            buf = f"{buf} {part}".strip() if buf else part
    if buf:
        pieces.append(buf)

    # Если даже по знакам не уложились — режем по словам.
    out: list[str] = []
    for p in pieces:
        if len(p) <= max_chars:
            out.append(p)
            continue
        words = p.split(" ")
        buf = ""
        for w in words:
            if buf and len(buf) + 1 + len(w) > max_chars:
                out.append(buf)
                buf = w
            else:
                buf = f"{buf} {w}".strip() if buf else w
        if buf:
            out.append(buf)
    return out


def _pack(sentences: list[str], max_chars: int) -> list[str]:
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split_long(s, max_chars))
            continue
        if buf and len(buf) + 1 + len(s) > max_chars:
            chunks.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip() if buf else s
    if buf:
        chunks.append(buf)
    return chunks


def chunk_chapter(
    text: str, max_chars: int = DEFAULT_MAX_CHARS, per_sentence: bool = False
) -> list[list[str]]:
    """Возвращает список абзацев, каждый — список фрагментов ≤ max_chars.

    per_sentence=True → каждое предложение становится отдельным фрагментом
    (длинные всё равно режутся). Тогда pipeline ставит паузу после каждого
    предложения, а не после пачки предложений. Синтез-вызовов больше, зато
    речь размереннее — для аудиокниги это часто приятнее.
    """
    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    if not paragraphs:
        return []
    out: list[list[str]] = []
    for para in paragraphs:
        single_line = re.sub(r"\s+", " ", para).strip()
        sentences = _sentences(single_line)
        if per_sentence:
            packed: list[str] = []
            for s in sentences:
                s = s.strip()
                if s:
                    packed.extend(_split_long(s, max_chars))
        else:
            packed = _pack(sentences, max_chars)
        if packed:
            out.append(packed)
    return out
