"""Презентация → кадры: разворачиваем анимацию в отдельные картинки.

Зачем это нужно. Экспорт .ppt/.pptx в PDF «схлопывает» слайд: все объекты
рисуются в конечном состоянии и накладываются друг на друга. Если на слайде
заголовок уходит по клику, а на его место выезжает другой, в PDF они окажутся
один поверх другого — читать невозможно. Для видео нужен не слайд, а
последовательность его состояний: то, что докладчик показывает нажатием Enter.

Как разворачиваем. Порядок анимации лежит в `<p:timing>` слайда: внутри
`mainSeq` каждый `<p:par>` верхнего уровня — один клик. Эффекты внутри клика
классифицируем по `presetClass`: `entr` (появление), `exit` (уход), остальное
(`emph`, пути) на видимость не влияет. Накапливая видимость от состояния к
состоянию, получаем список кадров и собираем новый pptx, где каждое состояние —
отдельный слайд с удалёнными невидимыми фигурами. Его и рендерим.

Отдельный случай — «карусель»: внутри одного клика фигура появляется, потом
уходит, и на её место приходит следующая (автоматическая смена картинок без
участия докладчика). Такой клик режем на подкадры: признак — уход фигуры,
которая появилась в этом же клике.

Рендер — через LibreOffice (headless, в PDF), затем PDF → PNG через PyMuPDF.
Никакого PowerPoint не требуется. Старый бинарный .ppt LibreOffice сначала
конвертирует в .pptx — анимация при этом сохраняется.
"""

from __future__ import annotations

import copy
import logging
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("zavuk.slides.frames")

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

for _prefix, _uri in (("a", A), ("p", P), ("r", R)):
    ET.register_namespace(_prefix, _uri[1:-1])

SHAPE_TAGS = ("sp", "pic", "graphicFrame", "grpSp", "cxnSp")

# Новые слайды нумеруем с запасом, чтобы не пересечься с исходными частями.
_NEW_SLIDE_BASE = 900


@dataclass
class Frame:
    """Одно состояние слайда — один кадр будущего видео."""

    seq: int  # сквозной номер кадра, с 1
    slide: int  # номер исходного слайда, с 1
    state: int  # 0 — слайд как открылся, дальше по одному на клик
    states_total: int
    image: Path | None = None


@dataclass
class Deck:
    frames: list[Frame] = field(default_factory=list)

    @property
    def slides(self) -> int:
        return len({f.slide for f in self.frames})

    def by_slide(self, slide: int) -> list[Frame]:
        return [f for f in self.frames if f.slide == slide]


# --------------------------------------------------------------------------
# разбор анимации
# --------------------------------------------------------------------------


def _tag(el) -> str:
    return el.tag.split("}")[-1]


def _top_shape_ids(root) -> list[str]:
    """id фигур верхнего уровня в порядке их следования в spTree."""
    tree = root.find(f"{P}cSld/{P}spTree")
    out: list[str] = []
    if tree is None:
        return out
    for child in tree:
        if _tag(child) in SHAPE_TAGS:
            cnv = next(child.iter(P + "cNvPr"), None)
            if cnv is not None and cnv.get("id"):
                out.append(cnv.get("id"))
    return out


def _clicks(root) -> list[list[tuple[str, str]]]:
    """Клики слайда: для каждого — список (id фигуры, 'entr' | 'exit' | 'other')."""
    timing = root.find(P + "timing")
    if timing is None:
        return []
    main_seq = next((c for c in timing.iter(P + "cTn") if c.get("nodeType") == "mainSeq"), None)
    if main_seq is None:
        return []
    lst = main_seq.find(P + "childTnLst")
    if lst is None:
        return []
    clicks = []
    for par in lst.findall(P + "par"):
        effects: list[tuple[str, str]] = []
        for ctn in par.iter(P + "cTn"):
            preset = ctn.get("presetClass")
            if not preset:
                continue
            kind = preset if preset in ("entr", "exit") else "other"
            seen: list[str] = []
            for tgt in ctn.iter(P + "spTgt"):
                sid = tgt.get("spid")
                if sid and sid not in seen:
                    seen.append(sid)
            effects.extend((sid, kind) for sid in seen)
        clicks.append(effects)
    return clicks


def visibility_states(root) -> list[list[str]]:
    """Состояния видимости слайда: список id фигур для каждого кадра."""
    ids = _top_shape_ids(root)
    clicks = _clicks(root)

    # Фигура видна с самого начала, если её первый эффект — не появление.
    first: dict[str, str] = {}
    for click in clicks:
        for sid, kind in click:
            first.setdefault(sid, kind)
    visible = [i for i in ids if first.get(i) != "entr"]
    states = [list(visible)]

    for click in clicks:
        current = set(visible)
        entered_here: set[str] = set()
        for sid, kind in click:
            # карусель: уходит то, что только что появилось в этом же клике —
            # значит кадр сменился сам, без нажатия
            if kind == "exit" and sid in entered_here:
                states.append([i for i in ids if i in current])
                entered_here = set()
            if kind == "entr":
                current.add(sid)
                entered_here.add(sid)
            elif kind == "exit":
                current.discard(sid)
        visible = [i for i in ids if i in current]
        states.append(list(visible))
    return states


# --------------------------------------------------------------------------
# сборка развёрнутой презентации
# --------------------------------------------------------------------------


def _replace_slide_number_fields(root, number: int) -> None:
    """Поле «номер слайда» → обычный текст.

    В развёрнутой презентации слайдов больше, чем в исходной, и поле показало бы
    сквозной номер кадра. Подставляем исходный номер слайда, чтобы нумерация в
    видео совпадала с раздаткой.
    """
    for parent in root.iter():
        for i, child in enumerate(list(parent)):
            if _tag(child) == "fld" and child.get("type") == "slidenum":
                run = ET.Element(A + "r")
                rpr = child.find(A + "rPr")
                if rpr is not None:
                    run.append(copy.deepcopy(rpr))
                ET.SubElement(run, A + "t").text = str(number)
                parent.remove(child)
                parent.insert(i, run)


def _build_slide(src_xml: bytes, keep_ids: list[str], number: int, font_map: dict[str, str]) -> bytes:
    text = src_xml.decode("utf-8")
    for missing, replacement in font_map.items():
        text = text.replace(f'typeface="{missing}"', f'typeface="{replacement}"')
    root = ET.fromstring(text.encode("utf-8"))

    tree = root.find(f"{P}cSld/{P}spTree")
    keep = set(keep_ids)
    for child in list(tree):
        if _tag(child) in SHAPE_TAGS:
            cnv = next(child.iter(P + "cNvPr"), None)
            if cnv is not None and cnv.get("id") not in keep:
                tree.remove(child)

    timing = root.find(P + "timing")
    if timing is not None:
        root.remove(timing)  # кадр статичен, анимация в нём уже не нужна

    _replace_slide_number_fields(root, number)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def _serialize(el, default_ns: str) -> bytes:
    """[Content_Types].xml и .rels требуют пространство имён по умолчанию."""
    ET.register_namespace("", default_ns)
    return ET.tostring(el, encoding="UTF-8", xml_declaration=True)


def expand_pptx(src: Path, dst: Path, font_map: dict[str, str] | None = None) -> list[Frame]:
    """Собирает pptx, где каждое состояние анимации — отдельный слайд."""
    font_map = font_map or {}
    zin = zipfile.ZipFile(src)
    names = sorted(
        (n for n in zin.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
        key=lambda s: int(re.search(r"\d+", s.rsplit("/", 1)[-1]).group()),
    )

    frames: list[Frame] = []
    parts: dict[str, tuple[bytes, str]] = {}
    for slide_no, name in enumerate(names, 1):
        raw = zin.read(name)
        states = visibility_states(ET.fromstring(raw))
        for state_no, keep in enumerate(states):
            seq = len(frames) + 1
            part = f"ppt/slides/slide{_NEW_SLIDE_BASE + seq}.xml"
            parts[part] = (_build_slide(raw, keep, slide_no, font_map), name)
            frames.append(Frame(seq=seq, slide=slide_no, state=state_no, states_total=len(states)))

    presentation = ET.fromstring(zin.read("ppt/presentation.xml"))
    sld_lst = presentation.find(P + "sldIdLst")
    for child in list(sld_lst):
        sld_lst.remove(child)
    rels = ET.fromstring(zin.read("ppt/_rels/presentation.xml.rels"))
    types = ET.fromstring(zin.read("[Content_Types].xml"))

    for frame, part in zip(frames, parts):
        rid = f"rIdFrame{frame.seq}"
        node = ET.SubElement(sld_lst, P + "sldId")
        node.set("id", str(600 + frame.seq))
        node.set(R + "id", rid)
        rel = ET.SubElement(rels, PR + "Relationship")
        rel.set("Id", rid)
        rel.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide")
        rel.set("Target", "slides/" + part.rsplit("/", 1)[-1])
        override = ET.SubElement(types, CT + "Override")
        override.set("PartName", "/" + part)
        override.set(
            "ContentType",
            "application/vnd.openxmlformats-officedocument.presentationml.slide+xml",
        )

    rewritten = {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "[Content_Types].xml"}
    # Читаем всё до открытия выходного архива: writestr(ZipInfo) правит служебные
    # поля переданного объекта, а они принадлежат исходному архиву.
    payload = [(i.filename, zin.read(i.filename)) for i in zin.infolist() if i.filename not in rewritten]
    rel_cache: dict[str, bytes] = {}
    for part, (_, src_name) in parts.items():
        src_rels = "ppt/slides/_rels/" + src_name.rsplit("/", 1)[-1] + ".rels"
        if src_rels not in rel_cache:
            node = ET.fromstring(zin.read(src_rels))
            for rel in list(node):
                # заметки ссылаются на слайд в обратную сторону; копии их не нужны
                if rel.get("Type", "").endswith("/notesSlide"):
                    node.remove(rel)
            rel_cache[src_rels] = _serialize(node, PR[1:-1])

    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in payload:
            zout.writestr(name, data)
        for part, (data, src_name) in parts.items():
            zout.writestr(part, data)
            src_rels = "ppt/slides/_rels/" + src_name.rsplit("/", 1)[-1] + ".rels"
            zout.writestr("ppt/slides/_rels/" + part.rsplit("/", 1)[-1] + ".rels", rel_cache[src_rels])
        zout.writestr("ppt/presentation.xml", ET.tostring(presentation, encoding="UTF-8", xml_declaration=True))
        zout.writestr("ppt/_rels/presentation.xml.rels", _serialize(rels, PR[1:-1]))
        zout.writestr("[Content_Types].xml", _serialize(types, CT[1:-1]))

    log.info("развёрнуто кадров: %d из %d слайдов", len(frames), len(names))
    return frames


# --------------------------------------------------------------------------
# рендер
# --------------------------------------------------------------------------


def find_soffice(explicit: str | None = None) -> str:
    """Путь к LibreOffice. Без него презентацию не отрисовать."""
    if explicit:
        return explicit
    found = shutil.which("soffice") or shutil.which("soffice.com")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.com"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.com"),
        Path("/usr/bin/soffice"),
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ):
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        "Не найден LibreOffice (soffice). Он нужен, чтобы отрисовать слайды; "
        "укажите путь параметром soffice."
    )


def _to_pdf(src: Path, out_dir: Path, soffice: str, profile: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            soffice,
            "--headless",
            "--norestore",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(f"LibreOffice не создал {pdf}")
    return pdf


def to_pptx(src: Path, work: Path, soffice: str, profile: Path) -> Path:
    """Старый бинарный .ppt → .pptx (анимация сохраняется)."""
    if src.suffix.lower() == ".pptx":
        return src
    subprocess.run(
        [
            soffice,
            "--headless",
            "--norestore",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            "pptx:Impress MS PowerPoint 2007 XML",
            "--outdir",
            str(work),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    out = work / (src.stem + ".pptx")
    if not out.exists():
        raise RuntimeError(f"LibreOffice не создал {out}")
    return out


def render(
    src: str | Path,
    work_dir: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    background: tuple[int, int, int] = (0, 0, 0),
    font_map: dict[str, str] | None = None,
    soffice: str | None = None,
) -> Deck:
    """Презентация → кадры-картинки нужного размера.

    Слайд вписывается в кадр целиком с полями по краям: у презентаций часто
    формат 4:3, а видео почти всегда показывают на 16:9.
    """
    from PIL import Image

    src = Path(src)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    profile = work / "loprofile"
    exe = find_soffice(soffice)

    pptx = to_pptx(src, work, exe, profile)
    expanded = work / "frames.pptx"
    frames = expand_pptx(pptx, expanded, font_map)
    pdf = _to_pdf(expanded, work / "pdf", exe, profile)

    import pymupdf

    images = work / "png"
    if images.exists():
        shutil.rmtree(images)
    images.mkdir(parents=True)

    doc = pymupdf.open(pdf)
    if doc.page_count != len(frames):
        raise RuntimeError(f"страниц в PDF {doc.page_count}, а кадров {len(frames)}")
    for frame, page in zip(frames, doc):
        # рендерим с запасом и уменьшаем — так мелкий текст на схемах читается
        zoom = 2 * height / page.rect.height
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        slide = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        scale = min(width / slide.width, height / slide.height)
        slide = slide.resize((round(slide.width * scale), round(slide.height * scale)), Image.LANCZOS)
        canvas = Image.new("RGB", (width, height), background)
        canvas.paste(slide, ((width - slide.width) // 2, (height - slide.height) // 2))
        frame.image = images / f"f{frame.seq:03d}.png"
        canvas.save(frame.image)

    log.info("отрисовано кадров: %d (%dx%d)", len(frames), width, height)
    return Deck(frames=frames)
