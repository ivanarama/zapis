"""Базовый класс облачных TTS-движков: HTTP-синтез → аудио → float32 PCM.

Общая логика для Яндекс SpeechKit и Сбер SaluteSpeech:
  • дробление длинного текста под лимит провайдера (по границам предложений);
  • POST-запрос с ретраями (429/5xx/сеть) и явным отказом на 401/403;
  • декод ответа в float32 mono PCM [-1, 1];
  • статус needs_config, когда учётные данные не заданы.

Подкласс реализует только `_credentials()` (чтение ключей из настроек) и
`_build_request()` (url/заголовки/тело/формат-декода под конкретного провайдера).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
import numpy as np

from .errors import CloudTtsError

log = logging.getLogger("zavuk.tts.cloud")

# Дробим по границам предложений (. ! ? …), сохраняя знак в куске.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def split_text(text: str, limit: int) -> list[str]:
    """Дробит текст на куски длиной <= limit по границам предложений.

    Предложение длиннее limit (без знаков препинания) режется жёстко по limit.
    Возвращает [] для пустого/пробельного текста.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    out: list[str] = []
    buf = ""
    for sent in _SENT_SPLIT.split(text):
        piece = (buf + " " + sent).strip() if buf else sent
        if len(piece) <= limit:
            buf = piece
            continue
        if buf:  # накопленный кусок готов, начинаем новый
            out.append(buf)
            buf = ""
        if len(sent) <= limit:
            buf = sent
        else:  # слишком длинное предложение — режем жёстко
            for i in range(0, len(sent), limit):
                out.append(sent[i:i + limit])
            buf = ""
    if buf:
        out.append(buf)
    return out


def decode_audio(content: bytes, fmt: str) -> np.ndarray:
    """Декодит ответ провайдера в float32 mono PCM [-1, 1].

    fmt == "lpcm": сырой int16 LE mono (Яндекс) — без декодера.
    Иначе (WAV/OGG/MP3) — через soundfile (libsndfile).
    Пустой ответ → пустой массив. Неизвестный/битый формат → CloudTtsError.
    """
    if not content:
        return np.zeros(0, dtype=np.float32)
    if fmt == "lpcm":
        arr = np.frombuffer(content, dtype="<i2")
        return (arr.astype(np.float32) / 32768.0).astype(np.float32)

    import io
    import soundfile as sf

    try:
        data, _sr = sf.read(io.BytesIO(content), dtype="float32", always_2d=False)
    except Exception as e:  # noqa: BLE001
        raise CloudTtsError(f"Не удалось декодировать аудио-ответ облака: {e}") from e
    if data.ndim > 1:  # стерео → моно
        data = data.mean(axis=1)
    return data.astype(np.float32)


class _CloudTtsEngine:
    """Общий каркас облачного движка. Переопределяется подклассами."""

    name = "cloud"
    accepts_accent_marks = False  # у облака своя нормализация текста
    sample_rate = 48000
    text_limit = 4900
    voices: list[str] = []
    default_voice: str = ""
    hifi: set[str] = set()  # голоса повышенного качества (для UI)

    # ---- точки расширения ----

    def _credentials(self) -> dict[str, Any]:
        """Читает учётные данные из настроек. Поднимает CloudTtsError, если их нет."""
        raise NotImplementedError

    def _build_request(self, text: str, voice: str, creds: dict[str, Any]):
        """Возвращает (url, headers, body, decode_fmt) под конкретного провайдера.

        body: dict (form-data) либо bytes/str (raw) — см. _post.
        decode_fmt: "lpcm" | "wav" | "ogg" | "mp3".
        """
        raise NotImplementedError

    # ---- транспорт ----

    def _post(self, url, headers, body, decode_fmt) -> np.ndarray:
        """POST с ретраями → float32 PCM. CloudTtsError на 401/403 и при истощении."""
        last_err: str | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=30.0) as client:
                    if isinstance(body, (bytes, bytearray)):
                        resp = client.post(url, headers=headers, content=bytes(body))
                    else:
                        resp = client.post(url, headers=headers, data=body)
                if resp.status_code in (401, 403):
                    raise CloudTtsError(
                        f"Ошибка авторизации облака (HTTP {resp.status_code}): "
                        "неверный ключ/токен"
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                resp.raise_for_status()
                return decode_audio(resp.content, decode_fmt)
            except CloudTtsError:
                raise
            except (httpx.HTTPError, OSError) as e:  # noqa: BLE001
                last_err = str(e)
                time.sleep(0.5 * (2 ** attempt))
        raise CloudTtsError(f"Облако недоступно после повторных попыток: {last_err}")

    # ---- контракт движка (тот же, что у Silero/Piper) ----

    def synth(self, text, speaker=None, sample_rate=None, **opts) -> np.ndarray:
        creds = self._credentials()  # CloudTtsError, если не настроено
        clean = (text or "").strip()
        if not clean:
            return np.zeros(0, dtype=np.float32)
        voice = speaker if speaker in self.voices else self.default_voice
        parts: list[np.ndarray] = []
        for piece in split_text(clean, self.text_limit):
            url, headers, body, fmt = self._build_request(piece, voice, creds)
            parts.append(self._post(url, headers, body, fmt))
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts).astype(np.float32)

    def initialize(self) -> None:
        """Готовность определяется наличием учётных данных — проверяется в get_status."""

    def resolve_sample_rate(self, speaker, requested) -> int:
        return self.sample_rate

    def list_speakers(self) -> list[str]:
        return list(self.voices)

    def get_status(self) -> dict:
        try:
            self._credentials()
            status, err = "ready", ""
        except CloudTtsError as e:
            status, err = "needs_config", str(e)
        return {
            "status": status,
            "engine": self.name,
            "speakers": list(self.voices),
            "hifi": sorted(self.hifi),
            "error": err,
        }
