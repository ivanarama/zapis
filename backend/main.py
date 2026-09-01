"""FastAPI: маршруты Zapis. ASR через фасад backend.asr, LLM через backend.llm."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import formats
from . import transcripts as transcript_store
from . import tts_runs
from .asr import factory as asr_factory
from .config import get_settings, save_settings, update_settings
from .llm import (
    PRESET_KEYS,
    build_messages_for_custom,
    build_messages_for_preset,
    complete_chat,
    default_prompts,
    format_llm_user_error,
    stream_chat,
)
from .schema import Settings
from .tts import export as tts_export
from .tts import normalize_cache as tts_norm_cache
from .tts import pipeline as tts_pipeline
from .tts.engine import SPEAKERS_V4_RU
from .tts.engine_piper import PIPER_RU_VOICES
from .tts.factory import get_engine as get_tts_engine
from .tts.normalize import normalize_text as tts_normalize_text
from .tts.reader import decode_book

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


def _apply_asr_device(settings: Settings) -> bool:
    """Прокидывает устройство из настроек в фабрику движков.

    Фабрика сама решает, менялось ли оно: при том же устройстве созданные
    движки (а с ними и прогретые модели) не сбрасываются.
    """
    changed = asr_factory.set_device(
        settings.asr.device,
        {
            "gigaam": settings.asr.gigaam.device,
            "whisper": settings.asr.whisper.device,
        },
    )
    if changed:
        log.info(
            "Устройство ASR: общее=%s, gigaam=%s, whisper=%s — движки пересоздадутся",
            settings.asr.device, settings.asr.gigaam.device, settings.asr.whisper.device,
        )
    return changed


@app.on_event("startup")
async def _startup():
    """Регистрируем активный ASR-движок и устройство. Саму модель НЕ грузим —
    она загрузится лениво при первой транскрибации (см. api_transcribe).
    Так открытие приложения ради озвучки не вкачивает ASR-модель в память."""
    settings = get_settings()
    # Устройство — до выбора движка: оно фиксируется при создании движка.
    _apply_asr_device(settings)
    asr_factory.set_active_engine(settings.asr.engine)


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
    # Модель не прогреваем — загрузится лениво при первой транскрибации.
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
            # Размер модели задаём ДО загрузки; сама initialize() выполнится
            # лениво внутри transcribe() в рабочем потоке, не блокируя loop.
            from .asr.whisper_engine import WhisperEngine
            if isinstance(eng, WhisperEngine):
                eng.set_model_size(settings.asr.whisper.model)
                eng.set_cpu_threads(settings.asr.whisper.cpu_threads)

        # ASR — CPU/GPU-bound, выносим в thread (там же ленивая инициализация)
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
        # Устройство применяем сразу: раньше смена CPU/GPU требовала
        # перезапуска приложения, хотя размер модели Whisper подхватывался на
        # лету. Прогретую модель это не роняет — движки пересоздаются только
        # при реальном изменении устройства.
        _apply_asr_device(new_settings)
        return {"ok": True, "settings": new_settings.model_dump()}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# Обратная совместимость со старым клиентом, который посылал POST /api/settings.
@app.post("/api/settings")
async def settings_post(request: Request):
    return await settings_put(request)


# ---------- Transcripts (сохранённые расшифровки) ----------


@app.get("/api/transcripts")
async def transcripts_list():
    return {"transcripts": transcript_store.list_transcripts()}


@app.get("/api/transcripts/{tid}")
async def transcripts_get(tid: str):
    try:
        rec = transcript_store.get_transcript(tid)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if rec is None:
        return JSONResponse({"error": "Расшифровка не найдена"}, status_code=404)
    return rec


class CreateTranscriptBody(BaseModel):
    name: str = ""
    source: str = ""
    engine: str = ""
    language: str = ""
    result: dict
    ai_blocks: list = Field(default_factory=list)


@app.post("/api/transcripts")
async def transcripts_create(body: CreateTranscriptBody):
    rec = transcript_store.create_transcript(
        name=body.name or "Без названия",
        source=body.source,
        engine=body.engine,
        language=body.language,
        result=body.result,
        ai_blocks=body.ai_blocks,
    )
    return {"ok": True, "transcript": rec}


class UpdateTranscriptBody(BaseModel):
    name: Optional[str] = None
    ai_blocks: Optional[list] = None


@app.put("/api/transcripts/{tid}")
async def transcripts_update(tid: str, body: UpdateTranscriptBody):
    patch: dict = {}
    if body.name is not None:
        patch["name"] = body.name
    if body.ai_blocks is not None:
        patch["ai_blocks"] = body.ai_blocks
    try:
        rec = transcript_store.update_transcript(tid, patch)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if rec is None:
        return JSONResponse({"error": "Расшифровка не найдена"}, status_code=404)
    return {"ok": True, "transcript": rec}


@app.delete("/api/transcripts/{tid}")
async def transcripts_delete(tid: str):
    try:
        ok = transcript_store.delete_transcript(tid)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not ok:
        return JSONResponse({"error": "Расшифровка не найдена"}, status_code=404)
    return {"ok": True}


# ---------- Export ----------


@app.post("/api/export/{fmt}")
async def api_export(fmt: str, request: Request):
    """Экспорт расшифровки. JSON результата — в теле POST-запроса."""
    try:
        result = await request.json()

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


# ---------- TTS (озвучивание текста → аудиокнига) ----------


DEFAULT_TTS_NORMALIZE_SYSTEM = (
    "Ты — нормализатор русского текста для синтеза речи (TTS). Перепиши текст так, "
    "чтобы его можно было произнести вслух, НЕ меняя смысл и ничего не сокращая:\n"
    "— числа, года, даты, римские цифры — словами в правильном падеже по контексту;\n"
    "— сокращения раскрывай полностью (т.е.→то есть, и т.д.→и так далее, "
    "г.→год или город по смыслу);\n"
    "— иностранные имена и названия — русской транскрипцией;\n"
    "— единицы измерения и символы (%, №, §) — словами.\n"
    "Не добавляй пояснений, заголовков и кавычек. Верни ТОЛЬКО переписанный текст, "
    "сохранив разбиение на абзацы."
)

_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def _audiobooks_dir() -> Path:
    base = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent
    )
    d = base / "audiobooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iter_text_windows(text: str, max_chars: int = 1500):
    """Окна из целых абзацев ≤ max_chars — чтобы LLM-вызов не был огромным."""
    buf = ""
    for para in _PARA_SPLIT_RE.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) > max_chars:
            if buf:
                yield buf
                buf = ""
            yield para  # один большой абзац — отдаём как есть
            continue
        if buf and len(buf) + 2 + len(para) > max_chars:
            yield buf
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        yield buf


def _llm_signature() -> str:
    """Сигнатура активной LLM-конфигурации для ключа кэша нормализации.

    Включает провайдеров, base_url и список моделей по профилям — при смене
    модели кэш инвалидируется. temperature не учитываем: нормализацию всегда
    зовём с temperature=0."""
    s = get_settings().llm
    parts = []
    for p in s.profiles:
        url = (p.base_url or s.base_url or "").strip().rstrip("/")
        models = ",".join(m.strip() for m in p.models if m.strip())
        parts.append(f"{p.api_provider}|{url}|{models}")
    return "\n".join(parts)


def _build_tts_normalizer():
    """Async-нормализатор через LLM с откатом на rule-based и защитой от отсебятины.

    Результаты модели кэшируются на диске (см. normalize_cache): повторная
    озвучка того же текста при тех же промпте/модели не дёргает LLM заново."""
    sys_prompt = (get_settings().prompts.tts_normalize.system or "").strip() or DEFAULT_TTS_NORMALIZE_SYSTEM
    signature = f"{sys_prompt}\x00{_llm_signature()}"

    async def _normalize(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return text
        out_windows: list[str] = []
        for window in _iter_text_windows(text):
            cached = tts_norm_cache.get(window, signature)
            if cached is not None:
                out_windows.append(cached)
                continue
            try:
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": window},
                ]
                result = (await complete_chat(messages, temperature=0)).strip()
                # Защита: нормализация обычно удлиняет текст; резкое укорачивание =
                # обрыв/отказ модели → откатываемся на rule-based для этого окна
                # и НЕ кэшируем (чтобы при следующем запуске получить полноценный).
                if not result or len(result) < 0.6 * len(window):
                    log.warning("LLM-нормализация подозрительно коротка — откат на rule-based.")
                    out_windows.append(tts_normalize_text(window))
                    continue
                tts_norm_cache.put(window, signature, result)
                out_windows.append(result)
            except Exception as e:  # noqa: BLE001
                log.warning("LLM-нормализация не удалась (%s) — откат на rule-based.", e)
                out_windows.append(tts_normalize_text(window))
        return "\n\n".join(out_windows)

    return _normalize


def _cloud_engine_info(name: str) -> dict:
    """Каталог голосов и флаг готовности облачного движка (для /api/tts/voices)."""
    st = get_tts_engine(name).get_status()
    return {
        "speakers": st["speakers"],
        "fixed_rate": True,  # частоту диктует провайдер
        "cloud": True,
        "needs_config": st["status"] == "needs_config",
        "hifi": st.get("hifi", []),
    }


@app.get("/api/tts/voices")
async def tts_voices():
    ts = get_settings().tts
    return {
        "engine": ts.engine,
        "engines": {
            # fixed_rate=True → частоту диктует движок, UI прячет выбор.
            "silero": {"speakers": SPEAKERS_V4_RU, "fixed_rate": False},
            "piper": {"speakers": PIPER_RU_VOICES, "fixed_rate": True},
            "yandex": _cloud_engine_info("yandex"),
            "sber": _cloud_engine_info("sber"),
            "edge": _cloud_engine_info("edge"),
        },
        "speakers": SPEAKERS_V4_RU,  # совместимость со старым фронтендом
        "tts": ts.model_dump(),
    }


@app.post("/api/tts/synthesize")
async def tts_synthesize(file: UploadFile = File(...), options: str = Form("{}")):
    """Озвучивание .txt → аудиокнига. SSE-стрим прогресса по главам/фрагментам."""
    data = await file.read()
    if not data:
        return JSONResponse({"error": "Файл пустой"}, status_code=400)
    text = decode_book(data, file.filename)
    if not text.strip():
        return JSONResponse({"error": "В файле нет текста"}, status_code=400)

    try:
        opts = json.loads(options) if options else {}
        if not isinstance(opts, dict):
            opts = {}
    except Exception:  # noqa: BLE001
        opts = {}

    ts = get_settings().tts
    title = (opts.get("title") or "").strip() or Path(file.filename or "Книга").stem or "Книга"
    author = (opts.get("author") or "").strip()
    engine = (opts.get("engine") or ts.engine or "silero").lower()
    if engine not in ("silero", "piper", "yandex", "sber", "edge"):
        engine = "silero"
    if engine == "piper":
        speaker = opts.get("speaker") or ts.piper.speaker
        # Частоту для Piper выбирает сам голос — pipeline согласует её (resolve).
        sample_rate = int(opts.get("sample_rate") or ts.silero.sample_rate)
        synth_opts = {"length_scale": float(opts.get("length_scale") or ts.piper.length_scale)}
    elif engine in ("yandex", "sber", "edge"):
        # Секреты движок берёт сам из settings (edge-tts — без секретов); в формуле
        # их НЕ передаём. Частоту диктует провайдер — pipeline согласует (resolve).
        if engine == "yandex":
            fallback_voice = ts.yandex.voice
        elif engine == "sber":
            fallback_voice = ts.sber.voice
        else:
            fallback_voice = ts.edge.voice
        speaker = opts.get("speaker") or fallback_voice
        sample_rate = 0
        synth_opts = {}
    else:
        speaker = opts.get("speaker") or ts.silero.speaker
        sample_rate = int(opts.get("sample_rate") or ts.silero.sample_rate)
        synth_opts = {"put_accent": ts.silero.put_accent, "put_yo": ts.silero.put_yo}
    audio_format = opts.get("format") or ts.export.format
    split_chapters = bool(opts.get("split_chapters", ts.export.split_chapters))
    bitrate = int(opts.get("bitrate") or ts.export.bitrate)
    use_llm = bool(opts.get("use_llm", ts.normalize.use_llm))
    accent = bool(opts.get("accent", ts.accent.enabled))
    per_sentence = bool(opts.get("pause_each_sentence", ts.pause_each_sentence))
    chapter_pattern = (ts.chapter_pattern or "").strip() or None

    # Запоминаем выбор со страницы «Озвучка» между сеансами (движок, голос,
    # частота, формат, разбивка, ударения, LLM). title/author — свойства книги, их
    # не сохраняем. Падение записи настроек не должно мешать синтезу.
    persist = {
        "engine": engine,
        "export": {"format": audio_format, "split_chapters": split_chapters},
        "normalize": {"use_llm": use_llm},
        "accent": {"enabled": accent},
        "pause_each_sentence": per_sentence,
    }
    if engine == "piper":
        persist["piper"] = {"speaker": speaker, "length_scale": synth_opts["length_scale"]}
    elif engine == "yandex":
        persist["yandex"] = {"voice": speaker}
    elif engine == "sber":
        persist["sber"] = {"voice": speaker}
    elif engine == "edge":
        persist["edge"] = {"voice": speaker}
    else:
        persist["silero"] = {"speaker": speaker, "sample_rate": sample_rate}
    try:
        update_settings({"tts": persist})
    except Exception:  # noqa: BLE001
        log.warning("Не удалось сохранить настройки озвучки", exc_info=True)

    # Уникальная папка на запуск — повтор той же книги не затирает предыдущую озвучку.
    out_dir = _audiobooks_dir() / f"{tts_export.sanitize_filename(title)}_{uuid.uuid4().hex[:8]}"
    normalizer = _build_tts_normalizer() if use_llm else None

    async def event_source():
        try:
            async for ev in tts_pipeline.synthesize(
                text=text,
                out_dir=out_dir,
                title=title,
                author=author,
                speaker=speaker,
                sample_rate=sample_rate,
                audio_format=audio_format,
                bitrate=bitrate,
                split_chapters=split_chapters,
                engine_name=engine,
                synth_opts=synth_opts,
                accent=accent,
                accent_model_size=ts.accent.model_size,
                per_sentence=per_sentence,
                pauses=ts.pauses.model_dump(),
                chapter_pattern=chapter_pattern,
                normalizer=normalizer,
            ):
                if ev.get("done"):
                    try:
                        run = tts_runs.create_run(
                            title=title, author=author, engine=engine, voice=speaker,
                            output_dir=ev.get("output_dir") or str(out_dir),
                            files=ev.get("files") or [],
                        )
                        ev["run_id"] = run["id"]
                    except Exception:  # noqa: BLE001
                        log.warning("Не удалось записать озвучку в историю", exc_info=True)
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            log.exception("Озвучивание не удалось")
            yield "data: " + json.dumps({"error": format_llm_user_error(exc)}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class TTSTestBody(BaseModel):
    engine: str = "silero"
    speaker: str = ""


@app.post("/api/tts/test")
async def tts_test(body: TTSTestBody):
    """Синтез короткой фразы — проверка настроек/ключей движка. Не бросает 5xx,
    чтобы UI мог прочитать JSON с ошибкой."""
    name = (body.engine or "").lower()
    if name not in ("silero", "piper", "yandex", "sber", "edge"):
        return JSONResponse({"ok": False, "error": "Неизвестный движок"}, status_code=400)
    try:
        eng = get_tts_engine(name)
        await asyncio.to_thread(eng.initialize)
        ts = get_settings().tts
        if name == "yandex":
            speaker = body.speaker or ts.yandex.voice
        elif name == "sber":
            speaker = body.speaker or ts.sber.voice
        elif name == "edge":
            speaker = body.speaker or ts.edge.voice
        elif name == "piper":
            speaker = body.speaker or ts.piper.speaker
        else:
            speaker = body.speaker or ts.silero.speaker
        sample_rate = eng.resolve_sample_rate(speaker, 0)
        audio = await asyncio.to_thread(eng.synth, "Проверка связи.", speaker, sample_rate)
        return {"ok": bool(len(audio)), "samples": int(len(audio))}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": format_llm_user_error(e)}, status_code=200)


class TTSRevealBody(BaseModel):
    path: str


@app.post("/api/tts/reveal")
async def tts_reveal(body: TTSRevealBody):
    """Открывает папку с результатом в проводнике."""
    p = Path(body.path)
    if not p.exists():
        return JSONResponse({"error": "Папка не найдена"}, status_code=404)
    try:
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["open", str(p)], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True}


@app.get("/api/tts/audio")
async def tts_audio(path: str):
    """Отдаёт готовый аудиофайл для встроенного плеера. Только из папки audiobooks."""
    root = _audiobooks_dir().resolve()
    p = Path(path).resolve()
    if p != root and root not in p.parents:
        return JSONResponse({"error": "Доступ запрещён"}, status_code=403)
    if not p.is_file():
        return JSONResponse({"error": "Файл не найден"}, status_code=404)
    return FileResponse(str(p))


@app.get("/api/tts/runs")
async def tts_runs_list():
    """История озвучек (зеркало списка расшифровок)."""
    return {"runs": tts_runs.list_runs()}


@app.get("/api/tts/runs/{rid}")
async def tts_runs_get(rid: str):
    rec = tts_runs.get_run(rid)
    if not rec:
        return JSONResponse({"error": "Озвучка не найдена"}, status_code=404)
    return rec


@app.delete("/api/tts/runs/{rid}")
async def tts_runs_delete(rid: str):
    if not tts_runs.delete_run(rid):
        return JSONResponse({"error": "Озвучка не найдена"}, status_code=404)
    return {"ok": True}


def run_server(host: str = "127.0.0.1", port: Optional[int] = None):
    import uvicorn
    if port is None:
        port = get_settings().app.port
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")
