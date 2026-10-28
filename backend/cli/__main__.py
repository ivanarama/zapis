"""Agent-friendly CLI: one JSON response on stdout, progress on stderr."""
from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.metadata
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import get_settings
from ..marketing.routes import service
from ..marketing.service import VideoRequest, contained
from ..marketing.voice import VoiceOptions
from .artifacts import describe_job, export_job
from .client import CliError, Client

EXAMPLES = {"edo-short": "Короткий ролик ЭДО", "edo": "Подробный ролик ЭДО", "motion": "Анимация схем и счётчика"}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("usage", message, 2)


def remote_options(parser):
    parser.add_argument("--no-start", action="store_true", help="не запускать Zapis автоматически")
    parser.add_argument("--quiet", action="store_true", help="скрыть прогресс в stderr")


def voice_options(parser):
    parser.add_argument("--engine", choices=["silero", "piper", "edge", "yandex", "sber"])
    parser.add_argument("--speaker")
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--accent", action="store_true")
    parser.add_argument("--length-scale", type=float, default=1.0)


def scenario_options(parser):
    parser.add_argument("campaign", nargs="?", help="JSON сценария, либо - для stdin")
    parser.add_argument("--example", choices=list(EXAMPLES))
    parser.add_argument("--assets", type=Path, help="корень файлов image/audio, по умолчанию папка JSON")


def build_parser():
    parser = Parser(prog="zapis", description=__doc__)
    groups = parser.add_subparsers(dest="group", required=True, parser_class=Parser)
    video = groups.add_parser("video", help="сценарий, TTS, раскадровка и MP4")
    commands = video.add_subparsers(dest="action", required=True, parser_class=Parser)
    commands.add_parser("doctor", help="проверить локальные зависимости без загрузки моделей")
    commands.add_parser("schema", help="JSON Schema сценария")
    commands.add_parser("examples", help="список встроенных примеров")
    example = commands.add_parser("example", help="получить пример вместе с файлами")
    example.add_argument("name", choices=list(EXAMPLES))
    example.add_argument("--output", type=Path)
    validate = commands.add_parser("validate", help="проверить сценарий и реальные файлы без TTS")
    scenario_options(validate)
    voices = commands.add_parser("voices", help="голоса движков без ключей и сетевых запросов")
    voices.add_argument("--engine", choices=["silero", "piper", "edge", "yandex", "sber"])
    preview = commands.add_parser("preview", help="короткая голосовая проба")
    text = preview.add_mutually_exclusive_group(required=True)
    text.add_argument("--text")
    text.add_argument("--text-file", type=Path)
    preview.add_argument("--output", type=Path)
    voice_options(preview)
    remote_options(preview)
    for name in ("storyboard", "render"):
        render = commands.add_parser(name, help="раскадровка" if name == "storyboard" else "ролик с голосом")
        scenario_options(render)
        voice_options(render)
        remote_options(render)
        render.add_argument("--preset", choices=["recording", "draft", "full"], default="recording")
        render.add_argument("--format", choices=["landscape", "portrait", "all"], default="landscape")
        render.add_argument("--no-wait", action="store_true", help="вернуть id и оставить сборку в Zapis")
        render.add_argument("--timeout", type=float, default=1800)
        render.add_argument("--output", type=Path, help="скопировать результаты в DIR/job-id/")
    jobs = commands.add_parser("jobs", help="история сборок")
    remote_options(jobs)
    for name in ("status", "wait", "artifacts"):
        item = commands.add_parser(name)
        item.add_argument("job_id")
        remote_options(item)
        if name == "wait":
            item.add_argument("--timeout", type=float, default=1800)
        if name in {"wait", "artifacts"}:
            item.add_argument("--output", type=Path)
    return parser


def selected_voice(args):
    settings = get_settings().tts
    engine = args.engine or settings.engine
    configured = getattr(settings, engine)
    speaker = args.speaker or getattr(configured, "speaker", None) or getattr(configured, "voice", None)
    return VoiceOptions(engine=engine, speaker=speaker, normalize=not args.no_normalize,
                        accent=args.accent, length_scale=args.length_scale)


def read_scenario(args):
    if args.example and args.campaign:
        raise CliError("usage", "Укажите campaign либо --example.", 2)
    if args.example:
        return service.example(args.example), args.assets.resolve() if args.assets else None
    if not args.campaign:
        raise CliError("usage", "Укажите JSON сценария либо --example.", 2)
    if args.campaign == "-":
        text = sys.stdin.read(1024 * 1024 + 1)
        if len(text.encode("utf-8")) > 1024 * 1024:
            raise CliError("input_too_large", "JSON сценария превышает 1 МБ.", 2)
        campaign = json.loads(text)
        assets = args.assets.resolve() if args.assets else Path.cwd()
    else:
        file = Path(args.campaign).resolve()
        if file.stat().st_size > 1024 * 1024:
            raise CliError("input_too_large", "JSON сценария превышает 1 МБ.", 2)
        campaign = json.loads(file.read_text(encoding="utf-8-sig"))
        assets = args.assets.resolve() if args.assets else file.parent
    if not isinstance(campaign, dict):
        raise CliError("invalid_scenario", "Сценарий должен быть объектом JSON.", 2)
    return campaign, assets


def imported_scenario(client, campaign, assets):
    result = copy.deepcopy(campaign)
    if assets is None:
        return result
    imported = {}
    for scene in result["scenes"]:
        for key in ("image", "audio"):
            if scene.get(key):
                source = scene[key]
                if source not in imported:
                    imported[source] = client.upload(contained(assets, source))
                scene[key] = imported[source]
    return result


def export_example(name, target):
    campaign = service.example(name)
    if target is None:
        return {"campaign": campaign}
    target = target.resolve()
    if target.exists():
        raise CliError("output_exists", "Сценарий уже существует: " + str(target), 4)
    copies = []
    for asset in {s[key] for s in campaign["scenes"] for key in ("image", "audio") if s.get(key)}:
        source, destination = service.asset_file(asset), contained(target.parent, asset)
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise CliError("output_exists", "Файл примера уже существует и отличается: " + str(destination), 4)
        copies.append((source, destination))
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(campaign, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"campaign_file": str(target), "assets_directory": str(target.parent), "assets": [str(d) for _, d in copies]}


def doctor():
    status = service.status()
    node = shutil.which("node")
    version = subprocess.check_output([node, "--version"], text=True).strip() if node else None
    status.update(python=sys.version.split()[0], python_executable=sys.executable, node_version=version,
                  project_directory=str(service.renderer.parent), storage_directory=str(service.storage))
    status["available"] = status["available"] and bool(version and int(version.lstrip("v").split(".")[0]) >= 22)
    status["packages"] = {}
    for package in ("fastapi", "piper-tts", "edge-tts", "torch", "soundfile"):
        try:
            status["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            status["packages"][package] = None
    if not status["available"]:
        raise CliError("dependencies", "Нужны Node.js 22+ и npm ci в video. Запустите PREPARE-VIDEO.cmd.", checks=status)
    return status


def run(args):
    action = args.action
    if action == "doctor":
        return doctor()
    if action == "schema":
        return json.loads((service.renderer / "campaign.schema.json").read_text(encoding="utf-8"))
    if action == "examples":
        return {"examples": [{"name": name, "description": title} for name, title in EXAMPLES.items()]}
    if action == "example":
        return export_example(args.name, args.output)
    if action == "voices":
        from ..tts.factory import get_engine
        settings = get_settings().tts
        result = {}
        for name in ([args.engine] if args.engine else ["silero", "piper", "edge", "yandex", "sber"]):
            result[name] = {"speakers": get_engine(name).list_speakers(), "online": name in {"edge", "yandex", "sber"}}
        return {"engines": result, "configured_engine": settings.engine}
    if action in {"validate", "storyboard", "render"}:
        campaign, assets = read_scenario(args)
        checked = service.validate(campaign, assets)
        if action == "validate":
            return checked
        if not math.isfinite(args.timeout) or args.timeout <= 0 or args.no_wait and args.output:
            raise CliError("usage", "timeout должен быть положительным. Для --no-wait используйте экспорт через video wait.", 2)
        request = VideoRequest(campaign=campaign, command=action, preset=args.preset, format=args.format, voice=selected_voice(args))
    if action == "preview":
        text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8-sig")
        if not 1 <= len(text.strip()) <= 420:
            raise CliError("usage", "Текст пробы должен содержать от 1 до 420 символов.", 2)
        options = selected_voice(args)
        if args.output and args.output.exists():
            raise CliError("output_exists", "Файл пробы уже существует: " + str(args.output), 4)
    if action == "wait" and (not math.isfinite(args.timeout) or args.timeout <= 0):
        raise CliError("usage", "timeout должен быть положительным.", 2)
    client = Client(service, no_start=args.no_start, progress=not args.quiet)
    client.ensure()
    if action == "preview":
        result = client.call("preview", {"text": text, "voice": options.model_dump()}, timeout=300)
        source = service.public_file(result["id"], "voice.wav")
        result["audio_file"] = str(source)
        result["audio"] = client.address + result["audio"]
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, args.output)
            result["saved_file"] = str(args.output.resolve())
        return result
    if action in {"storyboard", "render"}:
        request.campaign = imported_scenario(client, campaign, assets)
        job = client.call("jobs", request.model_dump())
        if not args.no_wait:
            job = client.wait(job["id"], args.timeout)
    elif action == "jobs":
        return {"jobs": [describe_job(service, job, client.address) for job in client.call("jobs")["jobs"]]}
    elif action == "wait":
        job = client.wait(args.job_id, args.timeout)
    else:
        job = client.call("jobs/" + args.job_id)
    result = describe_job(service, job, client.address)
    if getattr(args, "output", None):
        result["export"] = export_job(service, job, args.output)
    return result


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        # Keep stdout a single JSON document, even if an engine writes diagnostics.
        with contextlib.redirect_stdout(sys.stderr):
            result = run(args)
        response = {"schemaVersion": 1, "ok": True, "command": "video " + args.action, "result": result}
        code = 0
    except CliError as error:
        response = {"schemaVersion": 1, "ok": False, "error": {"code": error.code, "message": str(error), **error.details}}
        code = error.exit_code
    except (ValueError, TypeError, OSError) as error:
        response = {"schemaVersion": 1, "ok": False, "error": {"code": "invalid_input", "message": str(error)}}
        code = 2
    except KeyboardInterrupt:
        response = {"schemaVersion": 1, "ok": False, "error": {"code": "interrupted", "message": "Ожидание прервано. Запущенные задания остаются в Zapis."}}
        code = 130
    except Exception as error:
        response = {"schemaVersion": 1, "ok": False, "error": {"code": "internal_error", "message": str(error)}}
        code = 3
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
