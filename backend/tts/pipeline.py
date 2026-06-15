"""Оркестрация озвучивания: текст → главы → нормализация → нарезка → синтез →
склейка → экспорт.

Реализован как async-генератор событий прогресса (для SSE). Блокирующие шаги
(загрузка модели, синтез фрагмента, кодирование файла) выносятся в поток через
asyncio.to_thread, чтобы не блокировать event loop FastAPI.

Нормализация инъектируется параметром `normalizer` (async callable text→text):
  • None  → rule-based (backend.tts.normalize);
  • задан → LLM-нормализация (обёртку строит роут, он же делает фолбэк/защиту).
Так pipeline не зависит от backend.llm и легко тестируется с заглушкой.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from . import assemble, chunker, export
from . import chapters as chapters_mod
from . import normalize as normalize_mod
from .engine import get_engine

log = logging.getLogger("zavuk.tts.pipeline")

DEFAULT_PAUSES = {"sentence": 300, "paragraph": 700, "chapter": 1500}

Normalizer = Callable[[str], Awaitable[str]]


async def synthesize(
    *,
    text: str,
    out_dir: str | Path,
    title: str = "Книга",
    author: str = "",
    speaker: str = "baya",
    sample_rate: int = 48000,
    audio_format: str = "mp3",
    bitrate: int = 128000,
    split_chapters: bool = True,
    put_accent: bool = True,
    put_yo: bool = True,
    pauses: dict | None = None,
    chapter_pattern: str | None = None,
    normalizer: Normalizer | None = None,
) -> AsyncIterator[dict]:
    pauses = {**DEFAULT_PAUSES, **(pauses or {})}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = export.file_ext(audio_format)
    eng = get_engine(device="cpu")

    yield {"stage": "model", "message": "Загрузка модели синтеза…", "percent": 0}
    await asyncio.to_thread(eng.initialize)

    chapters = chapters_mod.split_chapters(text, chapter_pattern)
    total_ch = len(chapters)

    # --- Фаза 1: нормализация + нарезка (строим план, считаем фрагменты) ---
    prepared: list[tuple[str, list[list[str]]]] = []  # (title, paragraphs[chunks])
    total_chunks = 0
    for i, ch in enumerate(chapters, 1):
        yield {
            "stage": "prepare",
            "message": f"Подготовка текста: глава {i} из {total_ch}",
            "percent": int(2 + 10 * i / total_ch),
            "chapter": i,
            "chapters_total": total_ch,
        }
        norm_text = await normalizer(ch.text) if normalizer else normalize_mod.normalize_text(ch.text)
        paragraphs = chunker.chunk_chapter(norm_text)
        total_chunks += sum(len(p) for p in paragraphs)
        prepared.append((ch.title, paragraphs))

    if total_chunks == 0:
        yield {"stage": "error", "error": "Нет текста для озвучивания."}
        return

    # --- Фаза 2: синтез + склейка + экспорт ---
    done_chunks = 0
    files: list[str] = []
    all_parts: list = []  # для single-file режима

    for idx, (ch_title, paragraphs) in enumerate(prepared, 1):
        chapter_parts: list = []
        for para in paragraphs:
            for chunk in para:
                try:
                    audio = await asyncio.to_thread(
                        eng.synth, chunk, speaker, sample_rate, put_accent, put_yo
                    )
                except Exception as e:  # noqa: BLE001 — плохой фрагмент не должен рушить книгу
                    log.warning("Сбой синтеза фрагмента: %s", e)
                    audio = assemble.silence(pauses["sentence"], sample_rate)
                chapter_parts.append(audio)
                chapter_parts.append(assemble.silence(pauses["sentence"], sample_rate))
                done_chunks += 1
                yield {
                    "stage": "synth",
                    "message": f"Синтез: глава {idx} из {total_ch}",
                    "percent": int(12 + 83 * done_chunks / total_chunks),
                    "chapter": idx,
                    "chapters_total": total_ch,
                }
            chapter_parts.append(assemble.silence(pauses["paragraph"], sample_rate))

        chapter_audio = assemble.peak_normalize(assemble.concat(chapter_parts))

        if split_chapters:
            name = f"{idx:02d}. {export.sanitize_filename(ch_title, f'Глава {idx}')}.{ext}"
            meta = {"title": ch_title, "album": title, "artist": author, "track": str(idx)}
            await asyncio.to_thread(
                export.write_audio, out_dir / name, chapter_audio, sample_rate, audio_format, bitrate, meta
            )
            files.append(name)
            yield {"stage": "export", "message": f"Сохранена глава {idx}: {name}", "percent": int(12 + 83 * done_chunks / total_chunks)}
        else:
            all_parts.append(chapter_audio)
            all_parts.append(assemble.silence(pauses["chapter"], sample_rate))

    if not split_chapters:
        yield {"stage": "export", "message": "Сборка итогового файла…", "percent": 96}
        final_audio = assemble.concat(all_parts)
        name = f"{export.sanitize_filename(title)}.{ext}"
        meta = {"title": title, "album": title, "artist": author}
        await asyncio.to_thread(
            export.write_audio, out_dir / name, final_audio, sample_rate, audio_format, bitrate, meta
        )
        files = [name]

    yield {
        "stage": "done",
        "done": True,
        "message": f"Готово: {len(files)} файл(ов)",
        "percent": 100,
        "output_dir": str(out_dir),
        "files": files,
    }
