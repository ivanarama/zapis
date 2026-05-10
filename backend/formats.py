"""Унифицированные форматтеры результата транскрипции (TXT/SRT/VTT) и
сборка слов→сегментов. Используются всеми ASR-движками."""

from __future__ import annotations


def format_result(words: list[dict], pause_threshold: float = 0.5, language: str = "ru") -> dict:
    if not words:
        return {"text": "", "segments": [], "language": language}

    segments: list[list[dict]] = [[words[0]]]
    for i in range(1, len(words)):
        if words[i]["start"] - words[i - 1]["end"] > pause_threshold:
            segments.append([words[i]])
        else:
            segments[-1].append(words[i])

    result_segments = []
    for idx, seg_words in enumerate(segments):
        result_segments.append({
            "id": idx,
            "start": seg_words[0]["start"],
            "end": seg_words[-1]["end"],
            "text": " ".join(w["text"] for w in seg_words),
            "words": seg_words,
        })

    return {
        "text": " ".join(s["text"] for s in result_segments),
        "segments": result_segments,
        "language": language,
    }


def format_txt(result: dict) -> str:
    return result.get("text", "") or ""


def format_srt(result: dict) -> str:
    segments = result.get("segments", [])
    parts = []
    for idx, seg in enumerate(segments, 1):
        start = _ts_srt(seg["start"])
        end = _ts_srt(seg["end"])
        text = (seg.get("text") or "").strip()
        parts.append(f"{idx}\n{start} --> {end}\n{text}\n")
    return "\n".join(parts)


def format_vtt(result: dict) -> str:
    segments = result.get("segments", [])
    parts = ["WEBVTT", ""]
    for idx, seg in enumerate(segments, 1):
        start = _ts_vtt(seg["start"])
        end = _ts_vtt(seg["end"])
        text = (seg.get("text") or "").strip()
        parts.append(f"{idx}\n{start} --> {end}\n{text}\n")
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
