"""Сборка видео из презентации и сценария одной командой.

    python -m backend.slides презентация.ppt сценарий.docx -o видео.mp4

Сценарий — сплошной текст с пометками, где переключать показ: `[6]` — перейти
к шестому слайду, `[6$]` — нажать Enter внутри него. Ударения можно править
в словаре (--stress), он применяется последним и перебивает автоматические.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from ..tts.stress_dict import StressDict
from . import frames as frames_mod
from . import pipeline as pipeline_mod
from . import script as script_mod
from . import video as video_mod

log = logging.getLogger("zavuk.slides.cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend.slides", description=__doc__)
    p.add_argument("presentation", type=Path, help="файл .ppt или .pptx")
    p.add_argument("script", type=Path, help="сценарий .docx или .txt с пометками кадров")
    p.add_argument("-o", "--out", type=Path, default=Path("video.mp4"), help="куда сохранить видео")
    p.add_argument("--work", type=Path, default=None, help="рабочий каталог (по умолчанию рядом с видео)")

    p.add_argument("--engine", default="silero", help="silero | piper | edge | yandex | sber")
    p.add_argument("--speaker", default="baya", help="голос движка")
    p.add_argument("--speed", type=float, default=1.0, help="темп речи: 0.9 — медленнее, 1.1 — быстрее")
    p.add_argument("--sample-rate", type=int, default=48000)

    p.add_argument("--stress", type=Path, default=None, help="свой словарь ударений (json)")
    p.add_argument("--no-accent", action="store_true", help="не расставлять ударения автоматически")
    p.add_argument("--accent-model", default="tiny", help="размер модели ударений ruaccent")
    p.add_argument("--no-normalize", action="store_true", help="не раскрывать числа и сокращения словами")

    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=25)
    p.add_argument("--hold", type=int, default=6000, help="мс на кадр без собственного текста")
    p.add_argument(
        "--hold-frame",
        action="append",
        default=[],
        metavar="НОМЕР=МС",
        help="держать конкретный кадр указанное время (можно повторять)",
    )
    p.add_argument("--soffice", default=None, help="путь к LibreOffice, если не находится сам")
    p.add_argument(
        "--font",
        action="append",
        default=[],
        metavar="ИСХОДНЫЙ=ЗАМЕНА",
        help="подменить отсутствующий шрифт (можно повторять)",
    )
    p.add_argument("--headings", type=Path, default=None, help="файл со строками-рубриками, их не читать")
    p.add_argument("--markers", type=Path, default=None, help="json с исправлениями ошибочных пометок")
    p.add_argument("--dry-run", action="store_true", help="только разобрать и показать раскладку")
    return p


def _pairs(values: list[str], name: str) -> dict[str, str]:
    out = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"{name}: ожидается вид КЛЮЧ=ЗНАЧЕНИЕ, получено {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    work = args.work or args.out.parent / (args.out.stem + "_work")
    fonts = _pairs(args.font, "--font")
    holds = {int(k): int(v) for k, v in _pairs(args.hold_frame, "--hold-frame").items()}

    headings = set()
    if args.headings:
        headings = {line.strip() for line in args.headings.read_text(encoding="utf-8").splitlines() if line.strip()}
    overrides = {}
    if args.markers:
        raw = json.loads(args.markers.read_text(encoding="utf-8"))
        overrides = {k: (int(v["slide"]), bool(v.get("click", True))) for k, v in raw.items()}

    log.info("Разбираю презентацию…")
    deck = frames_mod.render(
        args.presentation,
        work,
        width=args.width,
        height=args.height,
        font_map=fonts,
        soffice=args.soffice,
    )
    log.info("Кадров: %d из %d слайдов", len(deck.frames), deck.slides)

    segments = script_mod.parse(
        script_mod.read_script(args.script), headings=headings, overrides=overrides
    )
    script_mod.bind(segments, deck.frames)

    if args.dry_run:
        for seg in segments:
            head = f"кадр {seg.seq:>3} | слайд {seg.slide}"
            if seg.state:
                head += f", шаг {seg.state}"
            print(f"\n### {head}\n{seg.text or '(молча)'}")
        covered = {s.seq for s in segments}
        silent = sorted(f.seq for f in deck.frames if f.seq not in covered)
        print(f"\nКадров без своего текста: {silent or 'нет'}")
        return 0

    stress = StressDict.load(args.stress) if args.stress else None
    if stress:
        log.info("Словарь ударений: %d записей", len(stress))

    log.info("Озвучиваю…")
    narration = pipeline_mod.narrate(
        segments,
        deck.frames,
        work / "voice.wav",
        engine_name=args.engine,
        speaker=args.speaker,
        sample_rate=args.sample_rate,
        accent=not args.no_accent,
        accent_model_size=args.accent_model,
        stress_dict=stress,
        normalize=not args.no_normalize,
        hold_ms=holds,
        default_hold_ms=args.hold,
        speed=args.speed,
        progress=lambda stage, i, n: log.info("  %s %d/%d", stage, i, n),
    )
    log.info("Длительность: %d:%02d", int(narration.seconds) // 60, int(narration.seconds) % 60)

    log.info("Собираю видео…")
    video_mod.build(
        narration.shots,
        narration.audio,
        args.out,
        fps=args.fps,
        title=args.out.stem,
        work_dir=work,
    )

    problems = video_mod.check_sync(args.out, narration.shots)
    if problems:
        log.warning("Картинка расходится с раскладкой:\n  %s", "\n  ".join(problems))
    else:
        log.info("Совпадение картинки и речи проверено.")
    log.info("Готово: %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
