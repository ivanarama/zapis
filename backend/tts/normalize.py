"""Rule-based нормализация текста для синтеза (без LLM).

Разворачивает числа в слова (num2words, именительный падеж) и расшифровывает
частые сокращения. Падежи и контекст — забота LLM-этапа (см. backend.llm);
здесь — быстрый офлайн-дефолт.
"""

from __future__ import annotations

import re

try:
    from num2words import num2words

    _HAS_NUM2WORDS = True
except Exception:  # noqa: BLE001
    _HAS_NUM2WORDS = False


# Сокращения. Ключи — то, что встречается в тексте; порядок применения —
# от длинных к коротким, чтобы «и т.д.» сработало раньше «т.д.».
_ABBREV = {
    "и т.д.": "и так далее",
    "и т. д.": "и так далее",
    "и т.п.": "и тому подобное",
    "и т. п.": "и тому подобное",
    "т.е.": "то есть",
    "т. е.": "то есть",
    "т.к.": "так как",
    "т. к.": "так как",
    "т.д.": "так далее",
    "т.п.": "тому подобное",
    "др.": "другие",
    "см.": "смотри",
    "стр.": "страница",
    "руб.": "рублей",
    "коп.": "копеек",
    "тыс.": "тысяч",
    "млн": "миллионов",
    "млрд": "миллиардов",
}

# Единицы после числа: «1990 г.» → «1990 год». Требуют предшествующего числа,
# поэтому обрабатываются отдельным проходом до разворачивания чисел.
_UNIT_AFTER_NUM = [
    (re.compile(r"(?<=\d)\s*гг\.", re.IGNORECASE), " годы"),
    (re.compile(r"(?<=\d)\s*г\.", re.IGNORECASE), " год"),
    (re.compile(r"(?<=\d)\s*вв\.", re.IGNORECASE), " века"),
    (re.compile(r"(?<=\d)\s*в\.", re.IGNORECASE), " век"),
]

_SYMBOLS = {"№": "номер ", "%": " процентов", "§": "параграф "}

_INT_RE = re.compile(r"\d+")


def _int_to_words(m: re.Match) -> str:
    if not _HAS_NUM2WORDS:
        return m.group()
    try:
        return num2words(int(m.group()), lang="ru")
    except Exception:  # noqa: BLE001
        return m.group()


def normalize_text(text: str) -> str:
    """Приводит текст к «произносимому» виду (rule-based)."""
    if not text:
        return text

    for k in sorted(_ABBREV, key=len, reverse=True):
        text = re.sub(re.escape(k), f" {_ABBREV[k]} ", text, flags=re.IGNORECASE)

    for pat, repl in _UNIT_AFTER_NUM:
        text = pat.sub(repl, text)

    for sym, repl in _SYMBOLS.items():
        text = text.replace(sym, repl)

    text = _INT_RE.sub(_int_to_words, text)

    # Схлопываем пробелы, появившиеся после замен.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +\n", "\n", text)
    return text
