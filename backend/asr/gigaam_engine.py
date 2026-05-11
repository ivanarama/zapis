# Source of truth: C:/Projects/gigaam_tone/transcribe.py — keep in sync
"""GigaAM v3 CTC + KenLM — обёртка для Zapis.

Ядро (load_audio, chunk_audio, GigaAMCTC, LongformCTC, CTCDecoderWithLM) синхронизировано
с gigaam_tone/transcribe.py — изменения в нём должны переноситься сюда.
"""

from __future__ import annotations

import gc
import logging
import subprocess
from dataclasses import dataclass
from itertools import groupby
from typing import Optional

import numpy as np
import torch

from ..formats import format_result
from .base import (
    EngineStatus,
    SAMPLE_RATE,
    TranscribeResult,
    decode_audio_bytes,
)

log = logging.getLogger("zapis.asr.gigaam")

GIGAAM_FREQ = 25


def _pairwise(iterable):
    it = iter(iterable)
    a = next(it, None)
    while a is not None:
        b = next(it, None)
        yield a, b
        a = b


@dataclass(frozen=True)
class AudioSegment:
    start_time: float
    end_time: float

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def audio_slice(self, sr: int = SAMPLE_RATE) -> slice:
        return slice(int(self.start_time * sr), int(self.end_time * sr))


def chunk_audio(
    length: float, segment_length: float = 30, segment_shift: float = 20,
) -> list[AudioSegment]:
    if length <= segment_length:
        return [AudioSegment(0, length)]

    segments: list[tuple[float, float]] = []
    for start in np.arange(0, length - segment_length, step=segment_shift):
        segments.append((float(start), float(start) + segment_length))

    last_start, last_end = segments[-1]
    if last_end < length and length > last_start + segment_shift:
        segments.append((last_start + segment_shift, length))

    return [AudioSegment(s, e) for s, e in segments]


def _groupby_into_spans(iterable):
    for key, group_iter in groupby(enumerate(iterable), key=lambda x: x[1]):
        group = list(group_iter)
        yield key, group[0][0], group[-1][0] + 1


def merge_ctc_log_probs_by_blank_sep(segments, log_probs, tick_size, blank_id):
    tick_spans = []
    for seg, lp in zip(segments, log_probs):
        start_ticks = round(seg.start_time / tick_size)
        tick_spans.append((start_ticks, start_ticks + len(lp)))

    overlap_sizes: list[int] = []
    deltas: list[int] = []

    for ((_s, end), cur_lp), ((nxt_s, _ne), nxt_lp) in _pairwise(
        zip(tick_spans, log_probs)
    ):
        overlap = end - nxt_s
        overlap_sizes.append(overlap)

        if overlap <= 0:
            deltas.append(0)
            continue

        blank_both = (
            (cur_lp[-overlap:].argmax(axis=1) == blank_id)
            & (nxt_lp[:overlap].argmax(axis=1) == blank_id)
        )
        if not np.any(blank_both):
            deltas.append(overlap // 2)
        else:
            blanks = [
                (i1, i2)
                for val, i1, i2 in _groupby_into_spans(blank_both)
                if val
            ]
            bs, be = max(blanks, key=lambda x: x[1] - x[0])
            deltas.append((be - 1 + bs) // 2)

    parts = []
    for idx, lp in enumerate(log_probs):
        cut_left = deltas[idx - 1] if idx > 0 else 0
        if idx < len(log_probs) - 1:
            cut_right = overlap_sizes[idx] - deltas[idx]
            parts.append(lp[cut_left:-cut_right] if cut_right > 0 else lp[cut_left:])
        else:
            parts.append(lp[cut_left:])

    return np.concatenate(parts, axis=0)


class GigaAMCTC:
    def __init__(self, version: str = "v3", device: str = "cuda", fp16: bool = False):
        import gigaam
        from gigaam.decoding import Tokenizer  # noqa: F401  (используется только для type hint в gigaam_tone)

        self._model = gigaam.load_model(
            f"{version}_ctc", fp16_encoder=fp16, device=device,
        )
        self._model.eval()

        tokenizer = self._model.decoding.tokenizer
        if tokenizer.charwise:
            vocab = list(self._model.decoding.tokenizer.vocab)
            vocab.insert(self._model.decoding.blank_id, "")
        else:
            from sentencepiece import SentencePieceProcessor  # noqa: F401

            sp = tokenizer.model
            vocab = [
                sp.IdToPiece(i).replace("▁", " ")
                for i in range(sp.GetPieceSize())
            ]

        self._vocab = tuple(vocab)
        self._blank_id: int = self._model.decoding.blank_id

    @property
    def blank_id(self) -> int:
        return self._blank_id

    @property
    def tick_size(self) -> float:
        return 1.0 / GIGAAM_FREQ

    @property
    def vocab(self) -> tuple[str, ...]:
        return self._vocab

    def ctc_log_probs(self, waveforms: list[np.ndarray]) -> list[np.ndarray]:
        from torch.nn.utils.rnn import pad_sequence

        with torch.inference_mode():
            device = self._model._device
            dtype = self._model._dtype

            tensors = [torch.tensor(w, dtype=dtype).to(device) for w in waveforms]
            lengths = torch.tensor(
                [len(w) for w in waveforms], device=device,
            )
            padded = pad_sequence(tensors, batch_first=True, padding_value=0)

            encoded, encoded_len = self._model.forward(padded, lengths)
            log_probs = self._model.head(encoder_output=encoded)

            return [
                lp[:length].cpu().numpy()
                for lp, length in zip(log_probs, encoded_len)
            ]


class LongformCTC:
    def __init__(
        self, shortform: GigaAMCTC,
        segment_length: float = 30,
        segment_shift: float = 20,
    ):
        self.shortform = shortform
        self.segment_length = segment_length
        self.segment_shift = segment_shift

    @property
    def blank_id(self) -> int:
        return self.shortform.blank_id

    @property
    def tick_size(self) -> float:
        return self.shortform.tick_size

    @property
    def vocab(self) -> tuple[str, ...]:
        return self.shortform.vocab

    def ctc_log_probs(self, waveforms: list[np.ndarray]) -> list[np.ndarray]:
        return [self._merge(w) for w in waveforms]

    def _merge(self, waveform: np.ndarray) -> np.ndarray:
        length = len(waveform) / SAMPLE_RATE
        segments = chunk_audio(length, self.segment_length, self.segment_shift)

        log_probs = []
        for seg in segments:
            part = waveform[seg.audio_slice()]
            log_probs.append(self.shortform.ctc_log_probs([part])[0])

        if len(segments) == 1:
            return log_probs[0]

        return merge_ctc_log_probs_by_blank_sep(
            segments, log_probs, self.shortform.tick_size, self.shortform.blank_id,
        )


@dataclass
class OutputBeam:
    text: str
    last_lm_state: object
    text_frames: list
    logit_score: float
    lm_score: float


class CTCDecoderWithLM:
    def __init__(
        self, ctc_model: LongformCTC, kenlm_path: str,
        alpha: float = 0.5, beta: float = 1.0, beam_width: int = 100,
        beam_prune_logp: float = -10, token_min_logp: float = -5,
    ):
        import pyctcdecode

        self.ctc_model = ctc_model
        self.beam_width = beam_width
        self.beam_prune_logp = beam_prune_logp
        self.token_min_logp = token_min_logp

        log.info("Loading KenLM model...")
        self.decoder = pyctcdecode.build_ctcdecoder(
            labels=list(ctc_model.vocab),
            kenlm_model_path=str(kenlm_path),
            alpha=alpha,
            beta=beta,
        )

    def timed_transcribe(self, waveform: np.ndarray) -> list[dict]:
        from pyctcdecode.constants import DEFAULT_PRUNE_BEAMS
        from pyctcdecode.language_model import HotwordScorer

        log_probs = self.ctc_model.ctc_log_probs([waveform])[0]
        log_probs = log_probs.clip(np.log(1e-15), 0)

        raw_beams = self.decoder._decode_logits(
            log_probs,
            beam_width=self.beam_width,
            beam_prune_logp=self.beam_prune_logp,
            token_min_logp=self.token_min_logp,
            prune_history=DEFAULT_PRUNE_BEAMS,
            hotword_scorer=HotwordScorer.build_scorer(None, weight=10.0),
            lm_start_state=None,
        )
        if not raw_beams:
            return []

        beams = []
        for b in raw_beams:
            if b is None:
                continue
            try:
                beams.append(OutputBeam(*b))
            except TypeError:
                log.warning("Skipping beam with unexpected structure: %s", type(b))

        if not beams:
            return []

        top = max(beams, key=lambda b: b.lm_score)

        if not top.text_frames:
            # Нет word-level таймстемпов — возвращаем весь текст как один сегмент
            text = top.text.strip() if top.text else ""
            if not text:
                return []
            dur = len(waveform) / SAMPLE_RATE
            return [{"text": text, "start": 0.0, "end": round(dur, 3)}]

        tick = self.ctc_model.tick_size
        words = []
        for frame in top.text_frames:
            if frame is None:
                continue
            try:
                word, (s, e) = frame
            except (TypeError, ValueError):
                continue
            words.append({"text": word, "start": round(s * tick, 3), "end": round(e * tick, 3)})
        return words


def _check_v3_available(version: str) -> bool:
    """Проверяет, поддерживает ли установленный gigaam нужную версию модели.

    Старый PyPI-gigaam держал список в `_MODEL_NAMES` (list), новый GitHub-gigaam
    переехал на `_MODEL_HASHES` (dict). Проверяем оба, чтобы поддержать обе ветки.
    """
    if version != "v3":
        return True
    import gigaam
    model_name = f"{version}_ctc"
    names = getattr(gigaam, "_MODEL_HASHES", None)
    if names is None:
        names = getattr(gigaam, "_MODEL_NAMES", ())
    return model_name in names


class GigaamEngine:
    """ASR-движок на базе GigaAM v3 CTC + KenLM (T-one)."""

    name = "gigaam"

    def __init__(self, version: str = "v3"):
        self._version = version
        self._model: Optional[GigaAMCTC] = None
        self._longform: Optional[LongformCTC] = None
        self._decoder: Optional[CTCDecoderWithLM] = None
        self._loaded = False
        self._error: Optional[str] = None
        self._needs_install = False

        if not _check_v3_available(version):
            import sys
            if getattr(sys, "frozen", False):
                self._error = "GigaAM v3 недоступен в этой сборке. Переключитесь на Whisper."
            else:
                self._needs_install = True

    def supported_languages(self) -> list[str]:
        return ["ru"]

    def get_status(self) -> EngineStatus:
        if self._needs_install:
            return {
                "status": "needs_install",
                "engine": self.name,
                "detail": f"GigaAM {self._version}",
                "install_hint": (
                    "Модель GigaAM v3 не установлена. "
                    "Нажмите, чтобы скачать пакет с GitHub и загрузить модель."
                ),
            }
        if self._error:
            return {"status": "error", "engine": self.name, "error": self._error}
        if self._loaded:
            return {"status": "ready", "engine": self.name, "detail": f"GigaAM {self._version}"}
        return {"status": "loading", "engine": self.name, "detail": f"GigaAM {self._version}"}

    def initialize(self) -> None:
        if self._loaded or self._error or self._needs_install:
            return
        try:
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            log.info("Using device: %s", device)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            log.info("Loading GigaAM %s CTC...", self._version)
            self._model = GigaAMCTC(self._version, device=str(device))
            self._longform = LongformCTC(self._model, segment_shift=20)

            log.info("Downloading T-one KenLM...")
            from huggingface_hub import hf_hub_download
            kenlm_path = hf_hub_download("t-tech/T-one", "kenlm.bin")

            log.info("Building CTC+LM decoder...")
            self._decoder = CTCDecoderWithLM(self._longform, kenlm_path)
            self._loaded = True
            log.info("GigaAM %s ready", self._version)
        except Exception as exc:
            log.exception("Failed to load GigaAM models")
            self._error = str(exc)

    def transcribe(
        self,
        file_bytes: bytes,
        filename: str,
        language: str = "ru",
    ) -> TranscribeResult:
        if self._error:
            raise RuntimeError(f"GigaAM не инициализирован: {self._error}")
        if not self._loaded or self._decoder is None:
            raise RuntimeError("Модель GigaAM ещё загружается")

        ext = filename.rsplit(".", maxsplit=1)[-1] if "." in filename else "wav"
        audio = decode_audio_bytes(file_bytes, ext)
        words = self._decoder.timed_transcribe(audio)
        return format_result(words, language="ru")

    def install_and_init(self) -> None:
        """Переустанавливает gigaam с GitHub и загружает модель."""
        if not self._needs_install:
            return
        import sys

        if getattr(sys, "frozen", False):
            self._error = (
                "Автоустановка недоступна в упакованном приложении. "
                "Запустите из исходного кода или пересоберите exe:\n"
                "venv\\Scripts\\activate && "
                "pip install --force-reinstall git+https://github.com/salute-developers/GigaAM.git"
            )
            self._needs_install = False
            return

        github_url = "git+https://github.com/salute-developers/GigaAM.git"
        log.info("Installing gigaam from GitHub...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--force-reinstall", github_url],
        )
        import importlib
        import gigaam
        importlib.reload(gigaam)
        self._needs_install = False
        self.initialize()
