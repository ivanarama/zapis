"""Кадры + озвучка → mp4.

Длительность кадра на экране равна длительности его фразы — поэтому картинка
всегда совпадает с речью, без ручной подгонки. Склейка делается concat-демуксером
ffmpeg: он принимает список картинок с длительностями, а дальше кодировщик сам
размножает кадры до постоянной частоты.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("zavuk.slides.video")


@dataclass
class Shot:
    """Кадр и сколько он держится на экране."""

    image: Path
    seconds: float


def _ffmpeg() -> str:
    import shutil

    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 — сообщение важнее типа
        raise FileNotFoundError("Не найден ffmpeg — он нужен для сборки видео.") from exc


def write_concat(shots: list[Shot], path: Path) -> Path:
    """Список для concat-демуксера.

    Последнюю картинку дублируем: демуксер игнорирует `duration` у последней
    записи и обрезал бы финальный кадр.
    """
    lines = []
    for shot in shots:
        lines.append(f"file '{shot.image.resolve().as_posix()}'")
        lines.append(f"duration {shot.seconds:.3f}")
    lines.append(f"file '{shots[-1].image.resolve().as_posix()}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def build(
    shots: list[Shot],
    audio: str | Path,
    out: str | Path,
    *,
    fps: int = 25,
    crf: int = 20,
    preset: str = "faster",
    audio_bitrate: str = "160k",
    gain: float = 0.85,
    title: str | None = None,
    work_dir: str | Path | None = None,
) -> Path:
    """Собирает mp4 из кадров и дорожки озвучки.

    `gain` слегка убирает громкость: синтез часто подходит вплотную к максимуму,
    а после кодирования в AAC такой сигнал может захрипеть на чужой аппаратуре.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir) if work_dir else out.parent
    work.mkdir(parents=True, exist_ok=True)
    listing = write_concat(shots, work / "frames_concat.txt")

    cmd = [
        _ffmpeg(), "-hide_banner", "-loglevel", "warning", "-y",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-i", str(audio),
        "-af", f"volume={gain}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", preset, "-tune", "stillimage",
        "-crf", str(crf), "-pix_fmt", "yuv420p", "-g", str(fps * 2),
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-shortest", "-movflags", "+faststart",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd.append(str(out))

    total = sum(s.seconds for s in shots)
    log.info("сборка видео: кадров %d, длительность %.1f c", len(shots), total)
    subprocess.run(cmd, check=True)
    return out


def check_sync(video: str | Path, shots: list[Shot], samples: int = 7, tolerance: float = 6.0) -> list[str]:
    """Сверяет несколько мест готового видео с исходными кадрами.

    Возвращает список расхождений — пустой список означает, что картинка
    совпадает с раскладкой. Дешёвая страховка от съехавшего тайминга.
    """
    import tempfile

    import numpy as np
    from PIL import Image

    bounds, t = [], 0.0
    for shot in shots:
        bounds.append((t, t + shot.seconds, shot.image))
        t += shot.seconds
    total = t
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(samples):
            at = total * (k + 0.5) / samples
            expected = next((img for a, b, img in bounds if a <= at < b), None)
            if expected is None:
                continue
            shot_png = Path(tmp) / f"{k}.png"
            subprocess.run(
                [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.3f}",
                 "-i", str(video), "-frames:v", "1", str(shot_png)],
                check=True,
            )
            got = np.asarray(Image.open(shot_png).convert("L").resize((160, 90)), dtype=float)
            want = np.asarray(Image.open(expected).convert("L").resize((160, 90)), dtype=float)
            diff = float(np.abs(got - want).mean())
            if diff > tolerance:
                problems.append(f"на {at:.1f} с ожидался {expected.name}, расхождение {diff:.1f}")
    return problems
