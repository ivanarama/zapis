"""Сценарий доклада → куски текста, привязанные к кадрам.

Докладчик пишет текст сплошняком и расставляет в нём пометки, где переключать
показ: `[6]` — перейти к шестому слайду, `[6$]` — нажать Enter внутри него
(следующий шаг анимации). Пометка стоит там, где происходит нажатие, то есть
обычно посреди фразы. Отсюда две особенности разбора:

* знак препинания, оставшийся в начале следующего куска, возвращаем предыдущему —
  иначе синтез начнёт фразу с точки;
* строки-рубрики, дословно повторяющие заголовок слайда, вслух не читаем: они
  и так напечатаны на экране.

Кусок может остаться без текста — например, когда две пометки стоят подряд.
Такой кадр просто держится на экране заданное время (см. `Segment.text == ""`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("zavuk.slides.script")

MARKER = re.compile(r"\[([^\]]*)\]")
_LEADING_PUNCT = re.compile(r"^([.,;:!?…]+)\s*")
_SENTENCE_END = ".!?…"


@dataclass
class Segment:
    """Текст, который звучит на одном кадре."""

    slide: int
    state: int  # 0 — слайд как открылся, дальше по одному на каждое нажатие
    text: str
    seq: int | None = None  # сквозной номер кадра, проставляется при связывании


def parse(
    paragraphs: list[str],
    *,
    headings: set[str] | None = None,
    overrides: dict[str, tuple[int, bool]] | None = None,
) -> list[Segment]:
    """Разбирает сценарий по пометкам.

    `headings` — строки-рубрики, которые не нужно читать вслух.
    `overrides` — пометки, записанные с ошибкой: текст пометки → (слайд, это ли
    нажатие). Пригождается, когда в сценарии опечатка, а сверить можно с числом
    кликов в самой презентации.
    """
    headings = headings or set()
    overrides = overrides or {}

    segments: list[Segment] = [Segment(slide=1, state=0, text="")]
    state_of: dict[int, int] = {}
    current_slide = 1
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            segments[-1].text = (segments[-1].text + " " + " ".join(buffer)).strip()
            buffer.clear()

    for para in paragraphs:
        line = re.sub(r"[ \t ]+", " ", para).strip()
        if not line or line in headings:
            continue
        line = line.lstrip("•").strip()
        pos = 0
        for m in MARKER.finditer(line):
            buffer.append(line[pos : m.start()])
            pos = m.end()
            flush()
            body = m.group(1).strip()
            if body in overrides:
                number, is_click = overrides[body]
            else:
                is_click = "$" in body
                digits = re.search(r"\d+", body)
                number = int(digits.group()) if digits else None
            if number is None:
                number = current_slide
            current_slide = number
            state_of[number] = state_of.get(number, 0) + 1 if is_click else 0
            segments.append(Segment(slide=number, state=state_of[number], text=""))
        buffer.append(line[pos:])
        buffer.append("")  # граница абзаца
    flush()

    for seg in segments:
        seg.text = re.sub(r"\s+", " ", seg.text).strip()

    # знак препинания с чужого куска возвращаем владельцу фразы
    for i in range(1, len(segments)):
        m = _LEADING_PUNCT.match(segments[i].text)
        if not m:
            continue
        j = i - 1
        while j >= 0 and not segments[j].text:
            j -= 1
        if j >= 0:
            if segments[j].text[-1] not in _SENTENCE_END:
                segments[j].text += m.group(1)
            segments[i].text = segments[i].text[m.end() :]

    for seg in segments:
        if seg.text and seg.text[-1] not in ".,;:!?…-—":
            seg.text += "."

    log.info("сценарий разобран: кусков %d", len(segments))
    return segments


def bind(segments: list[Segment], frames) -> list[Segment]:
    """Проставляет сегментам номера кадров и проверяет, что всё сошлось."""
    index = {(f.slide, f.state): f.seq for f in frames}
    missing = []
    for seg in segments:
        seg.seq = index.get((seg.slide, seg.state))
        if seg.seq is None:
            missing.append((seg.slide, seg.state))
    if missing:
        raise ValueError(
            "В сценарии есть пометки, которым не нашлось кадра: "
            + ", ".join(f"слайд {s}, шаг {st}" for s, st in missing)
            + ". Проверьте номера пометок и число нажатий на слайдах."
        )
    order = [s.seq for s in segments]
    if order != sorted(order):
        raise ValueError("Пометки в сценарии идут не по порядку кадров.")
    return segments


def read_docx(path) -> list[str]:
    """Абзацы из .docx без сторонних библиотек."""
    import xml.etree.ElementTree as ET
    import zipfile

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    return ["".join(t.text or "" for t in p.iter(w + "t")) for p in root.iter(w + "p")]


def read_script(path) -> list[str]:
    """Сценарий из .docx или .txt."""
    from pathlib import Path

    path = Path(path)
    if path.suffix.lower() == ".docx":
        return read_docx(path)
    return path.read_text(encoding="utf-8").splitlines()
