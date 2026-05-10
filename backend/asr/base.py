"""Базовые типы и интерфейс ASR-движка."""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict, runtime_checkable


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
