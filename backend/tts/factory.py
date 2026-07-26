"""Выбор движка синтеза по имени (зеркало backend.asr.factory).

Каждый движок (Silero, Piper) держит собственный синглтон в своём модуле и
реализует общий интерфейс: initialize / get_status / list_speakers /
resolve_sample_rate / synth(text, speaker, sample_rate, **opts) и атрибут
accepts_accent_marks. Pipeline работает с любым движком через этот интерфейс.
"""

from __future__ import annotations


def get_engine(name: str = "silero", device: str = "cpu"):
    """Возвращает синглтон движка по имени (silero | piper | yandex | sber)."""
    n = (name or "").lower()
    if n == "piper":
        from . import engine_piper

        return engine_piper.get_engine(device=device)
    if n == "yandex":
        from . import engine_yandex

        return engine_yandex.get_engine(device=device)
    if n == "sber":
        from . import engine_sber

        return engine_sber.get_engine(device=device)
    if n == "edge":
        from . import engine_edge

        return engine_edge.get_engine(device=device)
    from . import engine

    return engine.get_engine(device=device)
