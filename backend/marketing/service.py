from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .voice import VoiceOptions, synthesize

ID = re.compile(r"^[a-f0-9]{32}$")
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
PUBLIC_TYPES = IMAGE_TYPES | {".wav", ".mp4", ".json", ".srt", ".vtt", ".txt", ".html"}


class VideoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign: dict
    command: Literal["storyboard", "render"] = "storyboard"
    preset: Literal["recording", "draft", "full"] = "recording"
    format: Literal["landscape", "portrait", "all"] = "landscape"
    voice: VoiceOptions = Field(default_factory=VoiceOptions)


def contained(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or ":" in relative or "%" in relative:
        raise ValueError("Недопустимый путь к файлу.")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise ValueError("Недопустимый путь к файлу.")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Файл находится за пределами проекта.")
    return target


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class VideoService:
    def __init__(self, renderer: Path, storage: Path):
        self.renderer = renderer
        self.storage = storage
        self._busy = threading.Lock()
        self._active: set[str] = set()

    def status(self) -> dict:
        node = shutil.which("node")
        installed = (self.renderer / "node_modules/@remotion/renderer/package.json").is_file()
        available = bool(node and installed and (self.renderer / "scripts/backend.mjs").is_file())
        return {"available": available, "busy": self._busy.locked(), "node": bool(node), "renderer": installed,
                "project": hashlib.sha256(str(self.renderer.resolve()).encode()).hexdigest()[:16],
                "help": "Установите Node.js 22+ и выполните npm ci в папке video." if not available else ""}

    def example(self, name: str) -> dict:
        examples = {"edo-short": "examples/recording/campaign.json", "edo": "campaigns/edo.json", "motion": "campaigns/motion.json"}
        if name not in examples:
            raise ValueError("Неизвестный пример.")
        campaign = json.loads((self.renderer / examples[name]).read_text(encoding="utf-8"))
        # The old standalone demo used SAPI. Zapis supplies the voice instead.
        campaign["voice"] = {"name": "Zapis", "rate": 0, "gainDb": 0}
        for scene in campaign["scenes"]:
            scene.pop("audio", None)
        return campaign

    def _asset(self, name: str) -> Path:
        if name.startswith("assets/uploads/"):
            return contained(self.storage / "uploads", name.removeprefix("assets/uploads/"))
        return contained(self.renderer / "public", name)

    def asset_file(self, name: str) -> Path:
        source = self._asset(name)
        if source.suffix.lower() not in IMAGE_TYPES | {".wav"} or not source.is_file():
            raise FileNotFoundError(name)
        return source

    def upload(self, name: str, content: bytes) -> dict:
        suffix = Path(name).suffix.lower()
        if suffix not in IMAGE_TYPES | {".wav"}:
            raise ValueError("Поддерживаются PNG, JPG, WebP и WAV.")
        if not content or len(content) > 20 * 1024 * 1024:
            raise ValueError("Размер файла должен быть от 1 байта до 20 МБ.")
        asset = "assets/uploads/" + uuid.uuid4().hex + suffix
        path = self._asset(asset)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"asset": asset, "name": Path(name).name}

    def preview(self, text: str, voice: VoiceOptions) -> dict:
        identifier = uuid.uuid4().hex
        directory = self.storage / identifier
        result = synthesize(text, voice, directory / "voice.wav")
        result.update(id=identifier, created_at=datetime.now(timezone.utc).isoformat(),
                      audio=f"/api/video/files/{identifier}/voice.wav")
        write_json(directory / "preview.json", result)
        return result

    def previews(self) -> list[dict]:
        if not self.storage.exists():
            return []
        items = []
        for folder in self.storage.iterdir():
            if ID.fullmatch(folder.name) and (folder / "preview.json").is_file():
                items.append(json.loads((folder / "preview.json").read_text(encoding="utf-8")))
        return sorted(items, key=lambda item: item["created_at"], reverse=True)[:12]

    def _run_node(self, options: dict, directory: Path, *, progress=None) -> None:
        node = shutil.which("node")
        if not node:
            raise ValueError("Не найден Node.js. Установите версию 22 или новее.")
        request = directory / "renderer-request.json"
        write_json(request, options)
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        lines = []
        with subprocess.Popen([node, str(self.renderer / "scripts/backend.mjs"), str(request)], cwd=self.renderer,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                              errors="replace", env=environment, creationflags=flags) as process:
            for line in process.stdout:
                line = line.strip()
                if line:
                    lines.append(line)
                    if progress:
                        progress(line)
            code = process.wait()
        (directory / "renderer.log").write_text("\n".join(lines), encoding="utf-8")
        if code:
            raise ValueError("\n".join(lines[-6:]) or f"Ошибка сборки, код {code}.")

    def _snapshot(self, campaign: dict, directory: Path) -> dict:
        campaign = copy.deepcopy(campaign)
        scenes = campaign.get("scenes")
        if not isinstance(scenes, list) or not 1 <= len(scenes) <= 12:
            raise ValueError("Нужно от 1 до 12 сцен.")
        campaign["voice"] = {"name": "Zapis", "rate": 0, "gainDb": 0}
        assets = {"assets/fonts/NotoSans.ttf"}
        for scene in scenes:
            if not isinstance(scene, dict):
                raise ValueError("Каждая сцена должна быть объектом JSON.")
            if scene.get("image"):
                if Path(scene["image"]).suffix.lower() not in IMAGE_TYPES:
                    raise ValueError("Скриншот должен быть PNG, JPG или WebP.")
                assets.add(scene["image"])
            if scene.get("audio"):
                if Path(scene["audio"]).suffix.lower() != ".wav":
                    raise ValueError("Готовая озвучка должна быть WAV.")
                assets.add(scene["audio"])
        for name in assets:
            source = self._asset(name)
            target = contained(directory / "public", name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        write_json(directory / "campaign.json", campaign)
        return campaign

    def start(self, request: VideoRequest) -> dict:
        if not self.status()["available"]:
            raise ValueError(self.status()["help"])
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("Уже собирается другой ролик. Дождитесь завершения.")
        identifier = uuid.uuid4().hex
        directory = self.storage / identifier
        try:
            directory.mkdir(parents=True)
            campaign = self._snapshot(request.campaign, directory)
            # Validate the complete scenario and its assets BEFORE any paid TTS.
            options = {"command": "validate", "campaign": str(directory / "campaign.json"),
                       "publicDir": str(directory / "public"), "workDir": str(directory / "work"),
                       "buildDir": str(directory / "output"), "resultFile": str(directory / "result.json")}
            self._run_node(options, directory)
            meta = {"id": identifier, "created_at": datetime.now(timezone.utc).isoformat(), "status": "queued",
                    "message": "Сценарий проверен", "product": campaign["product"], "command": request.command,
                    "preset": request.preset, "format": request.format, "voice": request.voice.model_dump(), "outputs": []}
            write_json(directory / "job.json", meta)
            self._active.add(identifier)
            threading.Thread(target=self._execute, args=(directory, campaign, request, options, meta), daemon=True).start()
            return meta
        except Exception:
            self._busy.release()
            raise

    def _execute(self, directory, campaign, request, options, meta):
        def update(message, **extra):
            meta.update(message=message[:500], **extra)
            write_json(directory / "job.json", meta)
        try:
            update("Готовим сцены", status="running")
            takes = []
            if request.command == "render":
                for index, scene in enumerate(campaign["scenes"], 1):
                    update(f"Озвучка сцены {index} из {len(campaign['scenes'])}")
                    if not scene.get("audio"):
                        scene["audio"] = f"audio/{scene['id']}.wav"
                        takes.append(synthesize(scene["narration"], request.voice, directory / "public" / scene["audio"]))
                    else:
                        import numpy as np
                        import soundfile as sf
                        samples, _ = sf.read(directory / "public" / scene["audio"], dtype="float32")
                        if not samples.size or not np.isfinite(samples).all() or np.max(np.abs(samples)) < .0001:
                            raise ValueError(f"В WAV сцены {index} не обнаружена речь.")
                write_json(directory / "campaign.json", campaign)
                write_json(directory / "voice.json", {"takes": takes, "options": request.voice.model_dump()})
            options.update(command=request.command, preset=request.preset, format=request.format,
                           voice="files", tts=request.voice.model_dump())
            self._run_node(options, directory, progress=update)
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            outputs = []
            for job in result["jobs"]:
                base = f"/api/video/files/{meta['id']}/output/{job['directory']}/"
                outputs.append({**job, "review": base + "review.html", "poster": base + "poster.png",
                                "video": base + "video.mp4" if request.command == "render" else None,
                                "subtitles": base + "subtitles.vtt", "scenario": base + "scenario.json"})
            update("Готово", status="done", outputs=outputs)
        except Exception as error:
            update(str(error), status="error")
        finally:
            self._active.discard(meta["id"])
            self._busy.release()

    def job(self, identifier: str) -> dict:
        if not ID.fullmatch(identifier):
            raise ValueError("Неверный идентификатор сборки.")
        meta = json.loads((self.storage / identifier / "job.json").read_text(encoding="utf-8"))
        if meta["status"] in {"queued", "running"} and identifier not in self._active:
            meta.update(status="interrupted", message="Приложение перезапущено. Запустите новую сборку.")
        return meta

    def history(self) -> list[dict]:
        if not self.storage.exists():
            return []
        jobs = []
        for folder in self.storage.iterdir():
            if ID.fullmatch(folder.name) and (folder / "job.json").is_file():
                jobs.append(self.job(folder.name))
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)[:50]

    def public_file(self, identifier: str, relative: str) -> Path:
        if not ID.fullmatch(identifier):
            raise ValueError("Неверный идентификатор.")
        # Only artifacts and explicitly generated voice previews, never settings/logs.
        if not relative.startswith("output/") and relative != "voice.wav":
            raise ValueError("Файл недоступен.")
        file = contained(self.storage / identifier, relative)
        if file.suffix.lower() not in PUBLIC_TYPES or not file.is_file():
            raise FileNotFoundError(relative)
        return file
