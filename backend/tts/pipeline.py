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
from . import spool as spool_mod
from . import stress as stress_mod
from .factory import get_engine
from .errors import CloudTtsError

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
    engine_name: str = "silero",
    synth_opts: dict | None = None,
    accent: bool = False,
    accent_model_size: str = "tiny",
    per_sentence: bool = False,
    pauses: dict | None = None,
    chapter_pattern: str | None = None,
    normalizer: Normalizer | None = None,
) -> AsyncIterator[dict]:
    pauses = {**DEFAULT_PAUSES, **(pauses or {})}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = export.file_ext(audio_format)
    eng = get_engine(engine_name, device="cpu")

    yield {"stage": "model", "message": "Загрузка модели синтеза…", "percent": 0}
    await asyncio.to_thread(eng.initialize)
    # Частоту может диктовать сам движок (Piper — частотой голоса); согласуем ДО
    # нарезки и склейки, т.к. на ней считаются паузы, spool и кодирование.
    sample_rate = await asyncio.to_thread(eng.resolve_sample_rate, speaker, sample_rate)

    chapters = chapters_mod.split_chapters(text, chapter_pattern)
    total_ch = len(chapters)

    # --- Фаза 1: нормализация + ударения + нарезка (строим план, считаем фрагменты) ---
    # Ударения расставляем здесь, на нормализованном тексте (чтобы числа-словами
    # тоже получили «+»), и только для движков, понимающих «+»-разметку.
    accentizer = (
        stress_mod.get_accentizer(accent_model_size)
        if accent and getattr(eng, "accepts_accent_marks", True)
        else None
    )
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
        if accentizer is not None:
            norm_text = await asyncio.to_thread(accentizer.accentize, norm_text)
        paragraphs = chunker.chunk_chapter(norm_text, per_sentence=per_sentence)
        total_chunks += sum(len(p) for p in paragraphs)
        prepared.append((ch.title, paragraphs))

    # Выгружаем модель ударений до синтеза — на 8 ГБ незачем держать её в ОЗУ
    # одновременно с движком синтеза (см. backend.tts.stress / memory: dev-machine-ram).
    if accentizer is not None:
        await asyncio.to_thread(accentizer.unload)

    if total_chunks == 0:
        yield {"stage": "error", "error": "Нет текста для озвучивания."}
        return

    # --- Фаза 2: синтез + потоковая склейка + экспорт ---
    # Память держим постоянной: фрагменты главы копим во временном файле (spool),
    # затем читаем обратно блоками и кодируем потоково — не материализуя весь
    # звук книги в ОЗУ (см. backend.tts.spool / export.AudioStreamWriter).
    done_chunks = 0
    files: list[str] = []
    block = sample_rate * 10  # размер блока чтения spool, ~10 с аудио

    # В single-file режиме один writer открыт на всю книгу: главы дописываем
    # в него по очереди.
    single_writer: export.AudioStreamWriter | None = None
    if not split_chapters:
        name = f"{export.sanitize_filename(title)}.{ext}"
        meta = {"title": title, "album": title, "artist": author}
        single_writer = export.AudioStreamWriter(out_dir / name, sample_rate, audio_format, bitrate, meta)
        files = [name]

    try:
        for idx, (ch_title, paragraphs) in enumerate(prepared, 1):
            # Проход 1: синтез главы во временный буфер (память ~ один фрагмент).
            spool = spool_mod.PcmSpool(block)
            try:
                for para in paragraphs:
                    for chunk in para:
                        try:
                            audio = await asyncio.to_thread(
                                eng.synth, chunk, speaker, sample_rate, **(synth_opts or {})
                            )
                        except CloudTtsError:
                            # Сбой облака (нет ключа/сети/авторизации) — прерываем книгу
                            # с понятной ошибкой, иначе всё уйдёт в тишину.
                            raise
                        except Exception as e:  # noqa: BLE001 — плохой фрагмент не должен рушить книгу
                            log.warning("Сбой синтеза фрагмента: %s", e)
                            audio = assemble.silence(pauses["sentence"], sample_rate)
                        spool.write(audio)
                        spool.write(assemble.silence(pauses["sentence"], sample_rate))
                        done_chunks += 1
                        yield {
                            "stage": "synth",
                            "message": f"Синтез: глава {idx} из {total_ch}",
                            "percent": int(12 + 83 * done_chunks / total_chunks),
                            "chapter": idx,
                            "chapters_total": total_ch,
                        }
                    spool.write(assemble.silence(pauses["paragraph"], sample_rate))
                spool.finish()
                gain = spool.gain()  # глобальная по главе нормализация пика

                # Проход 2: кодирование главы из буфера (память ~ один блок).
                pct = int(12 + 83 * done_chunks / total_chunks)
                if split_chapters:
                    name = f"{idx:02d}. {export.sanitize_filename(ch_title, f'Глава {idx}')}.{ext}"
                    meta = {"title": ch_title, "album": title, "artist": author, "track": str(idx)}

                    def _encode_chapter(path=out_dir / name, meta=meta, spool=spool, gain=gain):
                        with export.AudioStreamWriter(path, sample_rate, audio_format, bitrate, meta) as w:
                            for blk in spool.blocks(gain):
                                w.write(blk)

                    await asyncio.to_thread(_encode_chapter)
                    files.append(name)
                    yield {"stage": "export", "message": f"Сохранена глава {idx}: {name}", "percent": pct}
                else:
                    def _append_chapter(spool=spool, gain=gain):
                        for blk in spool.blocks(gain):
                            single_writer.write(blk)
                        single_writer.write(assemble.silence(pauses["chapter"], sample_rate))

                    await asyncio.to_thread(_append_chapter)
                    yield {"stage": "export", "message": f"Глава {idx} из {total_ch} готова", "percent": pct}
            finally:
                spool.close()

        if single_writer is not None:
            yield {"stage": "export", "message": "Финализация файла…", "percent": 98}
            await asyncio.to_thread(single_writer.close)
            single_writer = None
    finally:
        # Подстраховка: при ошибке закрыть контейнер, чтобы не оставить открытый дескриптор.
        if single_writer is not None:
            try:
                single_writer.close()
            except Exception:  # noqa: BLE001
                pass

    yield {
        "stage": "done",
        "done": True,
        "message": f"Готово: {len(files)} файл(ов)",
        "percent": 100,
        "output_dir": str(out_dir),
        "files": files,
    }
