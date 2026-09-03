"""Унифицированные форматтеры результата транскрипции (TXT/SRT/VTT) и
сборка слов→сегментов. Используются всеми ASR-движками.

Здесь же живёт склейка с диаризацией: движки отдают слова с таймкодами,
диаризатор — интервалы «кто говорит», а связывает их сопоставление по времени
(см. assign_speakers). Благодаря этому диаризация одинаково работает и с
GigaAM, и с Whisper: ни один движок про неё ничего не знает.
"""

from __future__ import annotations

from typing import Optional

# Подпись говорящего в экспорте и в UI. Нумерация для человека — с единицы,
# внутри (в JSON) говорящие нумеруются с нуля.
SPEAKER_PREFIX = "Спикер"


def speaker_label(index: int) -> str:
    return f"{SPEAKER_PREFIX} {int(index) + 1}"


def assign_speakers(words: list[dict], turns: list[dict]) -> list[dict]:
    """Проставляет словам говорящего по максимальному перекрытию с репликами.

    Слова и реплики отсортированы по времени, поэтому идём двумя указателями:
    на часовой записи это тысячи слов против сотен реплик, и квадратичный
    перебор тут заметен глазом.

    Слова, не попавшие ни в одну реплику (диаризатор счёл это место паузой или
    шумом), получают говорящего ближайшего соседа — иначе посреди реплики
    появляются обрывки «без спикера».
    """
    if not words or not turns:
        return words

    ordered = sorted(turns, key=lambda t: (t["start"], t["end"]))
    base = 0
    for w in words:
        ws = float(w["start"])
        we = float(w["end"])
        if we <= ws:  # слово нулевой длины (бывает у Whisper) — берём мгновение
            we = ws + 1e-3

        while base < len(ordered) and ordered[base]["end"] < ws:
            base += 1

        best_speaker: Optional[int] = None
        best_overlap = 0.0
        i = base
        while i < len(ordered) and ordered[i]["start"] < we:
            overlap = min(we, ordered[i]["end"]) - max(ws, ordered[i]["start"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = int(ordered[i]["speaker"])
            i += 1
        w["speaker"] = best_speaker

    _fill_speaker_gaps(words)
    return words


def _fill_speaker_gaps(words: list[dict]) -> None:
    """Заполняет пропуски говорящим ближайшего по времени соседа.

    Именно ближайшего, а не предыдущего: пропуски возникают на стыке реплик,
    и слепое наследование «сверху» систематически приписывало бы первое слово
    новой реплики предыдущему говорящему.
    """
    n = len(words)
    prev_known: list[Optional[int]] = [None] * n
    next_known: list[Optional[int]] = [None] * n

    last: Optional[int] = None
    for i, w in enumerate(words):
        prev_known[i] = last
        if w.get("speaker") is not None:
            last = i

    nxt: Optional[int] = None
    for i in range(n - 1, -1, -1):
        next_known[i] = nxt
        if words[i].get("speaker") is not None:
            nxt = i

    for i, w in enumerate(words):
        if w.get("speaker") is not None:
            continue
        p, q = prev_known[i], next_known[i]
        if p is None and q is None:
            continue  # говорящих не нашлось вовсе — оставляем как есть
        if p is None:
            w["speaker"] = words[q]["speaker"]
        elif q is None:
            w["speaker"] = words[p]["speaker"]
        else:
            dist_prev = float(w["start"]) - float(words[p]["end"])
            dist_next = float(words[q]["start"]) - float(w["end"])
            w["speaker"] = words[p if dist_prev <= dist_next else q]["speaker"]


def format_result(
    words: list[dict],
    pause_threshold: float = 0.5,
    language: str = "ru",
    turns: Optional[list[dict]] = None,
) -> dict:
    """Собирает слова в сегменты. turns — разметка диаризации, если она была."""
    if not words:
        return {"text": "", "segments": [], "language": language}

    if turns:
        assign_speakers(words, turns)

    segments: list[list[dict]] = [[words[0]]]
    for i in range(1, len(words)):
        pause = words[i]["start"] - words[i - 1]["end"] > pause_threshold
        # Смена говорящего рвёт сегмент независимо от паузы: перебивают друг
        # друга обычно без пауз, а склеенная реплика двух людей бесполезна.
        speaker_changed = words[i].get("speaker") != words[i - 1].get("speaker")
        if pause or speaker_changed:
            segments.append([words[i]])
        else:
            segments[-1].append(words[i])

    result_segments = []
    for idx, seg_words in enumerate(segments):
        seg: dict = {
            "id": idx,
            "start": seg_words[0]["start"],
            "end": seg_words[-1]["end"],
            "text": " ".join(w["text"] for w in seg_words),
            "words": seg_words,
        }
        # Ключ speaker появляется только когда диаризация была: расшифровки без
        # неё должны выглядеть ровно как раньше.
        speaker = seg_words[0].get("speaker")
        if speaker is not None:
            seg["speaker"] = int(speaker)
        result_segments.append(seg)

    out = {
        "text": " ".join(s["text"] for s in result_segments),
        "segments": result_segments,
        "language": language,
    }
    speakers = {s["speaker"] for s in result_segments if s.get("speaker") is not None}
    if speakers:
        out["speakers"] = len(speakers)
        # Сплошной текст пересобираем с подписями: он уходит и в кнопку
        # «Скопировать», и в LLM, и в TXT — везде диалог должен читаться
        # диалогом, а не монологом без границ реплик.
        out["text"] = format_txt(out)
    return out


def apply_speakers(
    result: dict,
    turns: list[dict],
    pause_threshold: float = 0.5,
) -> dict:
    """Пересобирает готовый результат ASR с учётом разметки говорящих.

    Слова со всеми таймкодами уже лежат внутри сегментов, поэтому диаризацию
    можно применить постфактум — и не менять интерфейс ASR-движков ради
    необязательной функции.
    """
    words = [
        w
        for seg in (result.get("segments") or [])
        for w in (seg.get("words") or [])
    ]
    if not words or not turns:
        return result
    return format_result(
        words,
        pause_threshold=pause_threshold,
        language=result.get("language", "ru"),
        turns=turns,
    )


def _has_speakers(segments: list[dict]) -> bool:
    return any(s.get("speaker") is not None for s in segments)


def format_txt(result: dict) -> str:
    segments = result.get("segments") or []
    if not _has_speakers(segments):
        return result.get("text", "") or ""

    # Подряд идущие сегменты одного человека сливаем в абзац — иначе на каждую
    # паузу приходится по строке «Спикер 1:», и текст нечитаем.
    parts: list[str] = []
    current: Optional[int] = None
    buf: list[str] = []

    def flush():
        if buf:
            label = speaker_label(current) if current is not None else SPEAKER_PREFIX
            parts.append(f"{label}: " + " ".join(buf))

    for seg in segments:
        speaker = seg.get("speaker")
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        if speaker != current:
            flush()
            buf = []
            current = speaker
        buf.append(text)
    flush()
    return "\n\n".join(parts)


def _subtitle_text(seg: dict) -> str:
    text = (seg.get("text") or "").strip()
    speaker = seg.get("speaker")
    if speaker is None:
        return text
    return f"{speaker_label(speaker)}: {text}"


def format_srt(result: dict) -> str:
    segments = result.get("segments", [])
    parts = []
    for idx, seg in enumerate(segments, 1):
        start = _ts_srt(seg["start"])
        end = _ts_srt(seg["end"])
        parts.append(f"{idx}\n{start} --> {end}\n{_subtitle_text(seg)}\n")
    return "\n".join(parts)


def format_vtt(result: dict) -> str:
    segments = result.get("segments", [])
    parts = ["WEBVTT", ""]
    for idx, seg in enumerate(segments, 1):
        start = _ts_vtt(seg["start"])
        end = _ts_vtt(seg["end"])
        parts.append(f"{idx}\n{start} --> {end}\n{_subtitle_text(seg)}\n")
    return "\n".join(parts)


def _ts_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_vtt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
