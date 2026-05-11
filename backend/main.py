"""FastAPI: маршруты Zapis. ASR через фасад backend.asr, LLM через backend.llm."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import formats
from .asr import factory as asr_factory
from .config import get_settings, save_settings, update_settings
from .llm import (
    PRESET_KEYS,
    build_messages_for_custom,
    build_messages_for_preset,
    default_prompts,
    format_llm_user_error,
    stream_chat,
)
from .schema import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("zapis")


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


FRONTEND_DIR = _base_dir() / "frontend"

app = FastAPI(title="Zapis", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.on_event("startup")
async def _startup():
    """Инициализирует активный ASR-движок в фоне.

    GigaAM грузит модель на старте, чтобы первый запрос не ждал минуты.
    Whisper — наоборот, ленивая загрузка при первом transcribe()."""
    settings = get_settings()
    asr_factory.set_active_engine(settings.asr.engine)
    asr_factory.set_device(settings.asr.device)

    def _bg_init():
        try:
            engine = asr_factory.get_active_engine()
            # Ленивые движки (Whisper) initialize() сразу не вызываем.
            if engine.name == "gigaam":
                engine.initialize()
        except Exception:
            log.exception("Ошибка инициализации ASR")

    threading.Thread(target=_bg_init, daemon=True).start()


@app.get("/")
async def index():
    with open(FRONTEND_DIR / "index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/health")
async def health():
    engine = asr_factory.get_active_engine()
    return {"status": "ok", "asr": engine.get_status()}


@app.get("/api/asr/status")
async def asr_status():
    return asr_factory.get_active_engine().get_status()


@app.post("/api/asr/install")
async def asr_install():
    """Устанавливает пакет gigaam с GitHub и загружает модель."""
    engine = asr_factory.get_active_engine()
    from .asr.gigaam_engine import GigaamEngine
    if not isinstance(engine, GigaamEngine):
        return JSONResponse({"error": "Текущий движок не требует установки"}, status_code=400)
    if not engine._needs_install:
        return JSONResponse({"error": "Пакет уже установлен"}, status_code=400)

    def _bg():
        try:
            engine.install_and_init()
        except Exception:
            log.exception("Ошибка установки gigaam")

    threading.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "status": "installing"}


@app.get("/api/asr/engines")
async def asr_engines():
    settings = get_settings()
    out = []
    for name in asr_factory.available_engines():
        eng = asr_factory.get_engine(name)
        out.append({
            "name": name,
            "languages": eng.supported_languages(),
            "active": name == settings.asr.engine,
        })
    return {"engines": out, "active": settings.asr.engine, "language": settings.asr.language}


class SetEngineBody(BaseModel):
    engine: str
    language: Optional[str] = None


@app.post("/api/asr/engine")
async def asr_set_engine(body: SetEngineBody):
    try:
        asr_factory.set_active_engine(body.engine)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    patch: dict = {"asr": {"engine": body.engine}}
    if body.language:
        patch["asr"]["language"] = body.language
    update_settings(patch)
    # Запустим фоновую инициализацию для нового движка, если ему нужно.
    def _bg():
        try:
            eng = asr_factory.get_active_engine()
            if eng.name == "gigaam":
                eng.initialize()
        except Exception:
            log.exception("Ошибка инициализации ASR после смены движка")
    threading.Thread(target=_bg, daemon=True).start()
    return {"ok": True, "status": asr_factory.get_active_engine().get_status()}


@app.post("/api/transcribe")
async def api_transcribe(
    file: UploadFile = File(...),
    engine: Optional[str] = None,
    language: Optional[str] = None,
):
    try:
        data = await file.read()
        if not data:
            return JSONResponse({"error": "Файл пустой"}, status_code=400)

        settings = get_settings()
        engine_name = engine or settings.asr.engine
        lang = language or settings.asr.language

        eng = asr_factory.get_engine(engine_name)
        if engine_name == "whisper":
            # синхронная инициализация при первом обращении
            eng.initialize()
            # передадим выбранный размер модели
            from .asr.whisper_engine import WhisperEngine
            if isinstance(eng, WhisperEngine):
                eng.set_model_size(settings.asr.whisper.model)

        # ASR — CPU/GPU-bound, выносим в thread
        result = await asyncio.to_thread(eng.transcribe, data, file.filename, lang)
        return {"ok": True, "result": result}
    except Exception as e:
        log.exception("Transcription failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------- LLM ----------


class GenerateRequest(BaseModel):
    preset: Optional[str] = None
    custom_prompt: Optional[str] = None
    transcript: str = ""
    segments: list = Field(default_factory=list)


@app.post("/api/llm/generate")
async def llm_generate(req: GenerateRequest):
    """SSE-стриминг ответа LLM. Поддерживает 4 пресета и custom-сценарий."""
    try:
        if req.preset:
            if req.preset not in PRESET_KEYS:
                return JSONResponse({"error": f"Неизвестный пресет: {req.preset}"}, status_code=400)
            messages = build_messages_for_preset(req.preset, req.transcript, req.segments)
        elif req.custom_prompt:
            if not req.transcript.strip():
                return JSONResponse({"error": "Транскрипт пустой"}, status_code=400)
            messages = build_messages_for_custom(req.transcript, req.custom_prompt)
        else:
            return JSONResponse({"error": "Нужен preset или custom_prompt"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    async def event_source():
        try:
            async for token in stream_chat(messages):
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
            yield "data: " + json.dumps({"done": True}) + "\n\n"
        except Exception as exc:
            log.warning("LLM stream failed: %s", exc)
            err = format_llm_user_error(exc)
            yield "data: " + json.dumps({"error": err}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/llm/profiles")
async def llm_get_profiles():
    s = get_settings()
    return {
        "profiles": [p.model_dump() for p in s.llm.profiles],
        "temperature": s.llm.temperature,
        "max_tokens": s.llm.max_tokens,
    }


class ProfilesPayload(BaseModel):
    profiles: list[dict]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@app.put("/api/llm/profiles")
async def llm_put_profiles(body: ProfilesPayload):
    patch: dict = {"llm": {"profiles": body.profiles}}
    if body.temperature is not None:
        patch["llm"]["temperature"] = body.temperature
    if body.max_tokens is not None:
        patch["llm"]["max_tokens"] = body.max_tokens
    new_settings = update_settings(patch)
    return {"ok": True, "profiles": [p.model_dump() for p in new_settings.llm.profiles]}


# ---------- Prompts ----------


@app.get("/api/prompts")
async def prompts_get():
    s = get_settings().prompts
    return {
        "current": s.model_dump(),
        "defaults": default_prompts(),
    }


class PromptsPayload(BaseModel):
    prompts: dict


@app.put("/api/prompts")
async def prompts_put(body: PromptsPayload):
    new_settings = update_settings({"prompts": body.prompts})
    return {"ok": True, "prompts": new_settings.prompts.model_dump()}


# ---------- Settings (общие) ----------


@app.get("/api/settings")
async def settings_get():
    return get_settings().model_dump()


@app.put("/api/settings")
async def settings_put(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse({"error": "Ожидается объект"}, status_code=400)
    try:
        new_settings = Settings.model_validate(body)
        save_settings(new_settings)
        return {"ok": True, "settings": new_settings.model_dump()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# Обратная совместимость со старым клиентом, который посылал POST /api/settings.
@app.post("/api/settings")
async def settings_post(request: Request):
    return await settings_put(request)


# ---------- Export ----------


@app.get("/api/export/{fmt}")
async def api_export(fmt: str, text: str = ""):
    """Экспорт расшифровки. text — url-encoded JSON результата транскрипции."""
    try:
        result_json = urllib.parse.unquote(text)
        result = json.loads(result_json) if result_json else {}

        if fmt == "txt":
            content = formats.format_txt(result)
            media = "text/plain"
            filename = "transcript.txt"
        elif fmt == "srt":
            content = formats.format_srt(result)
            media = "text/plain"
            filename = "subtitles.srt"
        elif fmt == "vtt":
            content = formats.format_vtt(result)
            media = "text/vtt"
            filename = "subtitles.vtt"
        else:
            return JSONResponse({"error": "Неизвестный формат"}, status_code=400)

        return StreamingResponse(
            iter([content]),
            media_type=media,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        log.exception("Export failed")
        return JSONResponse({"error": str(e)}, status_code=500)


def run_server(host: str = "127.0.0.1", port: Optional[int] = None):
    import uvicorn
    if port is None:
        port = get_settings().app.port
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")
