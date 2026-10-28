from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..marketing.service import contained
from .client import CliError


def describe_job(service, job: dict, address: str) -> dict:
    result = dict(job)
    result["job_directory"] = str(service.storage / job["id"])
    outputs = []
    for output in job.get("outputs", []):
        item = dict(output)
        folder = contained(service.storage / job["id"] / "output", output["directory"])
        names = {"mp4": "video.mp4", "review": "review.html", "poster": "poster.png", "scenario": "scenario.json",
                 "manifest": "manifest.json", "srt": "subtitles.srt", "vtt": "subtitles.vtt", "narration": "narration.txt"}
        item["files"] = {key: str(folder / filename) for key, filename in names.items() if (folder / filename).is_file()}
        item["frames"] = [str(file) for file in sorted((folder / "frames").glob("*.png"))]
        item["audio"] = [str(file) for file in sorted((folder / "audio").glob("*.wav"))]
        for key in ("review", "poster", "video", "subtitles", "scenario"):
            if item.get(key):
                item[key] = address + item[key]
        outputs.append(item)
    result["outputs"] = outputs
    return result


def export_job(service, job: dict, destination: Path) -> dict:
    if job["status"] != "done":
        raise CliError("not_finished", "Сначала дождитесь успешного завершения сборки.", 5, job_id=job["id"])
    target = destination.resolve() / job["id"]
    copies = []
    for output in job["outputs"]:
        source = contained(service.storage / job["id"] / "output", output["directory"])
        for file in source.rglob("*"):
            if not file.is_file():
                continue
            resolved = file.resolve()
            if not resolved.is_relative_to(source.resolve()):
                raise CliError("unsafe_path", "В результатах обнаружена внешняя символическая ссылка.")
            relative = output["format"] + "/" + file.relative_to(source).as_posix()
            output_file = contained(target, relative)
            if output_file.exists() and output_file.read_bytes() != file.read_bytes():
                raise CliError("output_exists", "Экспорт содержит другой файл: " + str(output_file), 4)
            copies.append((file, output_file))
    for source, output_file in copies:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if not output_file.exists():
            shutil.copyfile(source, output_file)
    return {"directory": str(target), "files": [{"path": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
                                               for _, output in copies]}
