"""Сценарий + кадры → дорожка озвучки и раскладка кадров по времени.

Книжный pipeline (backend.tts.pipeline) устроен иначе: он льёт звук потоком и
длительности кусков наружу не отдаёт — для книги они не нужны. Здесь нужны
именно они, поэтому синтез идёт отдельно, но теми же деталями: движки через
factory, нормализация чисел, ударения ruaccent, нарезка chunker.

Две вещи, которых в книжном пути нет:

* края каждого куска подрезаются и сглаживаются. Движок отдаёт кусок с
  собственной тишиной по краям и обрывает звук не на нуле — на стыке с паузой
  слышен щелчок, а сама пауза выходит длиннее задуманной;
* свой словарь ударений (backend.tts.stress_dict) применяется последним, уже
  после ruaccent, — правку произношения слышит только человек, и последнее
  слово должно оставаться за ним.
"""

from __future__ import annotations

import logging
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..tts import assemble, chunker
from ..tts import normalize as normalize_mod
from ..tts import stress as stress_mod
from ..tts.factory import get_engine
from ..tts.stress_dict import StressDict
from .script import Segment
from .video import Shot

log = logging.getLogger("zavuk.slides.pipeline")

DEFAULT_PAUSES = {
    "phrase": 300,  # между фразами внутри слайда
    "slide": 550,  # при смене слайда
    "lead_in": 800,  # тишина в начале
}
DEFAULT_HOLD_MS = 6000  # сколько держать кадр, у которого нет своего текста

FADE_S = 0.012
EDGE_KEEP_S = 0.02


def trim_edges(x: np.ndarray, sample_rate: int, threshold: float = 0.004) -> np.ndarray:
    """Убрать тишину по краям куска, оставив небольшой запас."""
    peak = float(np.abs(x).max()) if x.size else 0.0
    if peak <= 0:
        return x
    loud = np.where(np.abs(x) > threshold * peak)[0]
    if loud.size == 0:
        return x
    keep = int(sample_rate * EDGE_KEEP_S)
    return x[max(0, loud[0] - keep) : min(len(x), loud[-1] + keep)]


def fade_edges(x: np.ndarray, sample_rate: int) -> np.ndarray:
    """Сгладить края, иначе на стыке с тишиной слышен щелчок."""
    n = min(int(sample_rate * FADE_S), len(x) // 2)
    if n < 2:
        return x
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def _atempo(x: np.ndarray, sample_rate: int, speed: float, work: Path) -> np.ndarray:
    """Изменить темп, не трогая высоту голоса (фильтр atempo ffmpeg).

    У офлайн-движков своего регулятора темпа нет, а синтез часто читает быстрее,
    чем комфортно слушать с экрана.
    """
    from .video import _ffmpeg

    src, dst = work / "_tempo_in.wav", work / "_tempo_out.wav"
    _write_wav(src, x, sample_rate)
    subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-filter:a", f"atempo={speed:.4f}", str(dst)],
        check=True,
    )
    return _read_wav(dst)


def _write_wav(path: Path, x: np.ndarray, sample_rate: int) -> None:
    data = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data.tobytes())


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0


@dataclass
class Narration:
    audio: Path
    shots: list[Shot]
    sample_rate: int
    seconds: float


def narrate(
    segments: list[Segment],
    frames,
    out_wav: str | Path,
    *,
    engine_name: str = "silero",
    speaker: str = "baya",
    sample_rate: int = 48000,
    synth_opts: dict | None = None,
    accent: bool = True,
    accent_model_size: str = "tiny",
    stress_dict: StressDict | None = None,
    normalize: bool = True,
    pauses: dict | None = None,
    hold_ms: dict[int, int] | None = None,
    default_hold_ms: int = DEFAULT_HOLD_MS,
    speed: float = 1.0,
    progress=None,
) -> Narration:
    """Озвучивает сегменты и раскладывает кадры по времени.

    `hold_ms` — сколько держать кадры без собственного текста (номер кадра → мс);
    для остальных берётся `default_hold_ms`.
    """
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    work = out_wav.parent
    pauses = {**DEFAULT_PAUSES, **(pauses or {})}
    hold_ms = hold_ms or {}
    synth_opts = synth_opts or {}

    frame_by_seq = {f.seq: f for f in frames}
    slide_of = {f.seq: f.slide for f in frames}
    anchors = sorted([s for s in segments if s.seq], key=lambda s: s.seq)
    if not anchors:
        raise ValueError("Сценарий не содержит ни одного куска текста.")

    eng = get_engine(engine_name, device="cpu")
    eng.initialize()
    sample_rate = eng.resolve_sample_rate(speaker, sample_rate)

    # Ударения — в фазе подготовки, до синтеза: модель ударений и движок синтеза
    # незачем держать в памяти одновременно (см. backend.tts.stress).
    accentizer = (
        stress_mod.get_accentizer(accent_model_size)
        if accent and getattr(eng, "accepts_accent_marks", True)
        else None
    )
    prepared: list[list[str]] = []
    for i, seg in enumerate(anchors, 1):
        text = seg.text
        if text:
            if normalize:
                text = normalize_mod.normalize_text(text)
            if accentizer is not None:
                text = accentizer.accentize(text)
            if stress_dict is not None:
                text = stress_dict.apply(text)
        # chunk_chapter отдаёт абзацы, каждый со своими кусками; кусок сценария
        # уже однороден, поэтому просто вытягиваем в один список
        prepared.append([c for para in chunker.chunk_chapter(text) for c in para] if text else [])
        if progress:
            progress("prepare", i, len(anchors))
    if accentizer is not None:
        accentizer.unload()

    pieces: list[np.ndarray] = [assemble.silence(pauses["lead_in"], sample_rate)]
    timeline: list[tuple[int, float]] = [(anchors[0].seq, pauses["lead_in"] / 1000)]
    last_seq = max(frame_by_seq)

    for i, (seg, chunks) in enumerate(zip(anchors, prepared)):
        nxt = anchors[i + 1].seq if i + 1 < len(anchors) else last_seq + 1
        span = list(range(seg.seq, nxt))  # кадры, которые делят этот кусок

        if chunks:
            parts: list[np.ndarray] = []
            for j, chunk in enumerate(chunks):
                audio = eng.synth(chunk, speaker=speaker, sample_rate=sample_rate, **synth_opts)
                audio = fade_edges(trim_edges(np.asarray(audio, dtype=np.float32), sample_rate), sample_rate)
                if j:
                    parts.append(assemble.silence(pauses["phrase"], sample_rate))
                parts.append(audio)
            voice = assemble.concat(parts)
            if abs(speed - 1.0) > 0.001:
                voice = _atempo(voice, sample_rate, speed, work)
            gap = pauses["slide"] if (i + 1 < len(anchors) and slide_of[anchors[i + 1].seq] != seg.slide) else pauses["phrase"]
            pieces.append(voice)
            pieces.append(assemble.silence(gap, sample_rate))
            seconds = len(voice) / sample_rate + gap / 1000
            each = seconds / len(span)
            timeline.extend((f, each) for f in span)
        else:
            seconds = sum(hold_ms.get(f, default_hold_ms) for f in span) / 1000
            pieces.append(assemble.silence(int(seconds * 1000), sample_rate))
            timeline.extend((f, hold_ms.get(f, default_hold_ms) / 1000) for f in span)

        if progress:
            progress("synth", i + 1, len(anchors))

    track = assemble.concat(pieces)
    _write_wav(out_wav, track, sample_rate)

    shots = [Shot(image=frame_by_seq[f].image, seconds=d) for f, d in timeline]
    total = sum(s.seconds for s in shots)
    log.info("озвучка готова: %.1f c, кадров в раскладке %d", total, len(shots))
    return Narration(audio=out_wav, shots=shots, sample_rate=sample_rate, seconds=total)
