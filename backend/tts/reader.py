r"""Чтение исходного текста книги из .txt / .rtf.

Русские .txt часто приходят в cp1251 — поэтому детектим кодировку перебором,
без внешних зависимостей (charset-normalizer в проект не тянем).

RTF (.rtf) разбираем своим компактным стриппером — иначе сырая разметка
({\rtf1\ansi...}, таблицы шрифтов, по \uNNNN? на каждую букву) уходила прямо в
синтез/LLM-нормализацию: модель захлёбывалась мусором, прогресса не было,
память на длинном файле уходила в OOM. Внешний striprtf не подключаем, чтобы не
тащить лишнюю зависимость в сборку Zapis.exe.
"""

from __future__ import annotations

import re

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "koi8-r", "cp866", "latin-1")


def decode_book(data: bytes, filename: str | None = None) -> str:
    """Извлекает читаемый текст книги из .txt или .rtf.

    Формат определяем по содержимому (сигнатуре RTF), имя файла — лишь подсказка:
    некоторые экспортеры дают .doc/.rtf вперемешку.
    """
    if _looks_like_rtf(data):
        return _normalize_newlines(decode_rtf(data))
    return decode_txt(data)


def _looks_like_rtf(data: bytes) -> bool:
    return data.lstrip()[:6].lower().startswith(b"{\\rtf")


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


# --- RTF ---------------------------------------------------------------------

# Группы-«назначения», текст которых не относится к телу документа и должен быть
# отброшен целиком (таблицы шрифтов/цветов/стилей, метаданные, картинки и т.п.).
_RTF_DESTINATIONS = frozenset((
    "fonttbl", "colortbl", "stylesheet", "listtable", "listoverridetable",
    "info", "title", "subject", "author", "keywords", "comment", "operator",
    "company", "manager", "category", "generator", "doccomm", "creatim",
    "revtim", "printim", "buptim", "filetbl", "rsidtbl", "xmlnstbl",
    "themedata", "colorschememapping", "latentstyles", "datastore",
    "pict", "object", "objdata", "header", "footer", "headerl", "headerr",
    "headerf", "footerl", "footerr", "footerf", "footnote", "annotation",
    "field", "fldinst", "datafield", "shppict", "nonshppict", "panose",
    "falt", "atnid", "atnauthor", "bkmkstart", "bkmkend", "mmath",
))

# Управляющие слова, которые порождают конкретный символ/перенос.
_RTF_SPECIAL = {
    # \par/\sect/\page завершают абзац → разделитель \n\n, по которому чанкер
    # ставит паузы между абзацами; \line — мягкий перенос внутри абзаца.
    "par": "\n\n", "sect": "\n\n", "page": "\n\n", "line": "\n",
    "tab": "\t", "cell": " ", "row": "\n\n", "lbr": "\n",
    "emdash": "—", "endash": "–",
    "lquote": "‘", "rquote": "’",
    "ldblquote": "“", "rdblquote": "”",
    "bullet": "•", "enspace": " ", "emspace": " ", "qmspace": " ",
}

_RTF_TOKEN = re.compile(
    r"\\([a-z]{1,32})(-?\d{1,10})?[ ]?"  # 1,2: управляющее слово (+ числовой аргумент)
    r"|\\'([0-9a-fA-F]{2})"               # 3: \'XX — байт в текущей кодовой странице
    r"|\\([^a-zA-Z])"                     # 4: \X — экранированный спецсимвол
    r"|([{}])"                            # 5: скобка группы
    r"|[\r\n]+"                           # переносы внутри RTF игнорируем
    r"|(.)",                              # 6: обычный символ
    re.DOTALL,
)

# \ansicpgNNNN → python-кодек для \'XX и обычных байтов.
_CODEPAGE = {
    "1251": "cp1251", "1252": "cp1252", "1250": "cp1250",
    "866": "cp866", "10007": "mac-cyrillic", "65001": "utf-8",
}


def _rtf_codec(head: str) -> str:
    m = re.search(r"\\ansicpg(\d+)", head)
    if m and m.group(1) in _CODEPAGE:
        return _CODEPAGE[m.group(1)]
    # Кириллический RTF без явной cpg почти всегда cp1251.
    return "cp1251"


def decode_rtf(data: bytes) -> str:
    """Извлекает текст из RTF (без внешних зависимостей).

    Реализация — компактный конечный автомат поверх RTF-спецификации:
    обрабатывает группы и игнорируемые назначения ({\\*...}), \\ucN/\\uNNNN,
    \\'XX в кодовой странице из \\ansicpg, \\par/\\line → переносы.
    """
    text = data.decode("latin-1")  # RTF — 7-битный ASCII-каркас; байты >127 даёт \'XX
    codec = _rtf_codec(text[:512])

    out: list[str] = []
    # стек хранит (ucskip, ignorable) на момент входа в группу
    stack: list[tuple[int, bool]] = []
    ignorable = False  # внутри игнорируемого назначения
    ucskip = 1         # сколько символов пропустить после \uNNNN (из \ucN)
    curskip = 0        # счётчик «съедаемых» fallback-символов после \u

    for m in _RTF_TOKEN.finditer(text):
        word, arg, hexc, char, brace, tchar = m.groups()
        if brace:
            curskip = 0
            if brace == "{":
                stack.append((ucskip, ignorable))
            elif stack:
                ucskip, ignorable = stack.pop()
        elif char is not None:        # \X
            curskip = 0
            if char == "~":
                if not ignorable:
                    out.append(" ")
            elif char in "{}\\":
                if not ignorable:
                    out.append(char)
            elif char == "*":
                ignorable = True
            elif char in "\r\n":
                if not ignorable:
                    out.append("\n")
        elif word is not None:        # \word или \word123
            curskip = 0
            if word in _RTF_DESTINATIONS:
                ignorable = True
            elif word == "uc":
                ucskip = int(arg) if arg else 1
            elif word == "u":
                if not ignorable:
                    c = int(arg)
                    if c < 0:
                        c += 0x10000
                    out.append(chr(c))
                curskip = ucskip
            elif not ignorable and word in _RTF_SPECIAL:
                out.append(_RTF_SPECIAL[word])
            # прочие управляющие слова (форматирование) молча отбрасываем
        elif hexc is not None:        # \'XX
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(bytes([int(hexc, 16)]).decode(codec, errors="replace"))
        elif tchar is not None:       # обычный символ
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(tchar)

    # RTF не различает мягкий перенос абзаца и «новую строку»; схлопываем
    # подряд идущие \par в разделитель абзацев, который понимает чанкер.
    result = "".join(out)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{2,}", "\n\n", result)
    return result.strip()
