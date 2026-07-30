"""Базовые типы и интерфейс ASR-движка."""

from __future__ import annotations

import os
import socket
import ssl
import tempfile
import urllib.error
from typing import Iterator, Literal, Optional, Protocol, TypedDict, runtime_checkable

import numpy as np

SAMPLE_RATE = 16_000

# Хост, с которого качаются веса Whisper и языковая модель KenLM.
HUGGINGFACE_HOST = "huggingface.co"
# Веса GigaAM качаются с CDN Сбера, а НЕ с HuggingFace (см. gigaam/__init__.py:
# _URL_DIR). Доступность HF про этот хост ничего не говорит — поэтому в тексте
# ошибки его надо называть явно.
GIGAAM_CDN_HOST = "cdn.chatwm.opensmodel.sberdevices.ru"


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Исключение и всё, что к нему привело (__cause__/__context__)."""
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        cur = cur.__cause__ or cur.__context__


def _network_reason(exc: BaseException) -> Optional[str]:
    """Причина сетевого сбоя человеческими словами, либо None, если ошибка
    вообще не про сеть (нехватка памяти, битый файл, баг в модели)."""
    for err in _exception_chain(exc):
        name = type(err).__name__

        if isinstance(err, ssl.SSLCertVerificationError) or name == "SSLCertVerificationError":
            return (
                "не прошла проверка TLS-сертификата — обычно это корпоративный "
                "прокси, подменяющий сертификаты"
            )
        if isinstance(err, ssl.SSLError) or name.startswith("SSL"):
            return "ошибка TLS-соединения"

        # urllib (GigaAM) и httpx (huggingface_hub) сообщают HTTP-код по-разному.
        code = err.code if isinstance(err, urllib.error.HTTPError) else None
        if code is None:
            code = getattr(getattr(err, "response", None), "status_code", None)
        if isinstance(code, int):
            if code in (401, 403, 407):
                return f"сервер ответил {code} — доступ закрыт или прокси требует авторизации"
            return f"сервер ответил {code}"

        if isinstance(err, (socket.timeout, TimeoutError)) or "Timeout" in name:
            return "истёк таймаут соединения"
        if isinstance(err, (ConnectionError, socket.gaierror)) or name in (
            "URLError", "ConnectError", "ProxyError", "ReadError", "RemoteProtocolError",
        ):
            return "соединение не устанавливается — хост недоступен или закрыт файрволом"
    return None


def describe_download_error(exc: BaseException, what: str, host: str) -> Optional[str]:
    """Объяснение сбоя скачивания модели с указанием конкретного хоста.

    Без этого в UI улетает голый str(exc), и пользователь на другом конце
    сообщает «не качается, хотя сайт открывается» — про какой именно сайт
    речь, по такому тексту не понять. Возвращает None, если исключение не
    сетевое: тогда вызывающий код показывает исходное сообщение как есть.
    """
    reason = _network_reason(exc)
    if reason is None:
        return None
    detail = str(exc).strip().replace("\n", " ")
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return (
        f"Не удалось скачать {what}: {reason}. "
        f"Проверьте доступ к {host} (интернет, прокси, файрвол) и попробуйте снова. "
        f"Подробности — в zapis.log рядом с приложением. "
        f"[{type(exc).__name__}: {detail}]"
    )


def decode_audio_bytes(data: bytes, ext: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Декодирует аудио/видео в float32 mono PCM нужной частоты через pyav.

    pyav приходит транзитивной зависимостью faster-whisper, поэтому мы
    избавлены от внешнего ffmpeg.exe в PATH (раньше оба движка падали с
    FileNotFoundError на машинах без ffmpeg).
    """
    import av  # локальный импорт, чтобы не тянуть pyav при импорте пакета

    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
        f.write(data)
        tmp_path = f.name

    try:
        with av.open(tmp_path) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise ValueError("В файле не найден аудио-поток")

            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=sample_rate,
            )
            chunks: list[np.ndarray] = []
            for frame in container.decode(stream):
                for r in resampler.resample(frame):
                    chunks.append(r.to_ndarray())
            for r in resampler.resample(None):  # flush
                chunks.append(r.to_ndarray())

        if not chunks:
            return np.zeros(0, dtype=np.float32)
        pcm = np.concatenate(chunks, axis=1).reshape(-1)
        return pcm.astype(np.float32) / 32768.0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class EngineStatus(TypedDict, total=False):
    status: Literal["idle", "loading", "ready", "error", "needs_install"]
    engine: str
    detail: str
    error: str
    install_hint: str


class TranscribeResult(TypedDict):
    text: str
    segments: list
    language: str


@runtime_checkable
class Transcriber(Protocol):
    """Протокол ASR-движка. Реализации должны быть устойчивы к повторным
    вызовам initialize() — повторный init не должен переинициализировать
    уже загруженную модель."""

    name: str

    def initialize(self) -> None: ...

    def get_status(self) -> EngineStatus: ...

    def transcribe(
        self,
        file_bytes: bytes,
        filename: str,
        language: str = "ru",
    ) -> TranscribeResult: ...

    def supported_languages(self) -> list[str]: ...
