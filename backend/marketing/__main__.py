"""Repeat the same pipeline without browser interaction."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from .routes import service
from .service import VideoRequest
from .voice import VoiceOptions
from ..config import get_settings


def main():
    parser = argparse.ArgumentParser(description="Zapis: сценарий + скриншоты + голос → видео")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--campaign", type=Path, help="JSON сценария. Файлы относительно video/public.")
    source.add_argument("--example", choices=["edo-short", "edo", "motion"], default="edo-short")
    parser.add_argument("--command", choices=["storyboard", "render"], default="storyboard")
    parser.add_argument("--engine", choices=["silero", "piper", "edge", "yandex", "sber"], default="piper")
    parser.add_argument("--speaker", default="ru_RU-irina-medium")
    parser.add_argument("--preset", choices=["recording", "draft", "full"], default="recording")
    parser.add_argument("--format", choices=["landscape", "portrait", "all"], default="landscape")
    args = parser.parse_args()
    campaign = json.loads(args.campaign.read_text(encoding="utf-8-sig")) if args.campaign else service.example(args.example)
    request = VideoRequest(campaign=campaign, command=args.command, preset=args.preset, format=args.format,
                           voice=VoiceOptions(engine=args.engine, speaker=args.speaker))
    address = f"http://127.0.0.1:{get_settings().app.port}/api/video"
    def call(endpoint, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(address + endpoint, data=data, headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)
    try:
        running = call("/status")
    except (OSError, urllib.error.URLError):
        running = None
    remote = running and running.get("project") == service.status()["project"]
    # Reuse the application's queue so CLI and UI cannot start competing jobs.
    job = call("/jobs", request.model_dump()) if remote else service.start(request)
    last = None
    while True:
        job = call("/jobs/" + job["id"]) if remote else service.job(job["id"])
        if job["message"] != last:
            last = job["message"]
            print(last, flush=True)
        if job["status"] in {"done", "error", "interrupted"}:
            break
        time.sleep(1)
    print(f"Результаты: {service.storage / job['id']}")
    if job["status"] != "done":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
