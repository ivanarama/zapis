"""One narration path for voice samples and complete campaigns."""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from ..config import get_settings
from ..slides.pipeline import _write_wav, fade_edges, trim_edges
from ..tts.chunker import chunk_chapter
from ..tts.factory import get_engine
from ..tts.normalize import normalize_text

_lock = threading.Lock()


class VoiceOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["silero", "piper", "edge", "yandex", "sber"] = "piper"
    speaker: str = Field(default="ru_RU-irina-medium", min_length=1, max_length=100)
    normalize: bool = True
    accent: bool = False
    length_scale: float = Field(default=1.0, ge=0.5, le=2.0)


def synthesize(text: str, options: VoiceOptions, output: Path) -> dict:
    """Use existing Zapis voices, rejecting silent or broken provider replies."""
    with _lock:
        engine = get_engine(options.engine, device="cpu")
        if options.speaker not in engine.list_speakers():
            raise ValueError("Выберите голос из списка этого движка.")
        engine.initialize()
        text = normalize_text(text) if options.normalize else text.strip()
        settings = get_settings().tts
        if options.accent and getattr(engine, "accepts_accent_marks", False):
            from ..tts.stress import get_accentizer
            accentizer = get_accentizer(settings.accent.model_size)
            try:
                text = accentizer.accentize(text)
            finally:
                accentizer.unload()
        rate = engine.resolve_sample_rate(options.speaker, 48000)
        synthesis = {}
        if options.engine == "piper":
            synthesis["length_scale"] = options.length_scale
        elif options.engine == "silero":
            synthesis = {"put_accent": settings.silero.put_accent, "put_yo": settings.silero.put_yo}
        takes = []
        for paragraph in chunk_chapter(text):
            for chunk in paragraph:
                raw = np.asarray(engine.synth(chunk, speaker=options.speaker, sample_rate=rate, **synthesis), dtype=np.float32)
                if raw.ndim != 1 or not raw.size or not np.isfinite(raw).all() or float(np.max(np.abs(raw))) < 0.0001:
                    raise ValueError("Движок вернул тишину вместо речи. Выберите другой голос или повторите пробу.")
                if takes:
                    takes.append(np.zeros(round(rate * 0.18), dtype=np.float32))
                takes.append(fade_edges(trim_edges(raw, rate), rate))
        if not takes:
            raise ValueError("Текст озвучки пуст.")
        samples = np.concatenate(takes)
        output.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(output, samples, rate)
        return {"engine": options.engine, "speaker": options.speaker, "sampleRate": rate,
                "seconds": round(len(samples) / rate, 3), "text": text,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
