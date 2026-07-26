"""Яндекс SpeechKit TTS-движок: облачный синтез русской речи.

Альтернатива Silero/Piper уровня «живой диктор»: голоса SpeechKit (alena, filipp,
Hi-Fi nastya/maxim/…) звучат заметно естественнее локальных моделей и не грузят
CPU/CPU-инференсом — синтез идёт в облаке Яндекса. Тарифицируется по Yandex Cloud
(есть бесплатный стартовый грант); пользователь подставляет свой api_key+folder_id.

Синтез: POST tts.api.cloud.yandex.net/speech/v1/tts:synthesize, form-data,
format=lpcm → сырой int16 LE mono PCM (декодер не нужен). Длинный текст дробится
базовым классом под лимит Яндекса (~5000 символов на запрос). Не понимает
«+»-ударения (своя нормализация) → accepts_accent_marks = False.
"""

from __future__ import annotations

import logging
import threading

from .engine_cloud_base import _CloudTtsEngine
from .errors import CloudTtsError

log = logging.getLogger("zavuk.tts.engine_yandex")

# Русские голосы SpeechKit (v1). Источник: доки Yandex SpeechKit → список голосов.
# Стандартные + Hi-Fi (улучшенное качество). Каталог может меняться — правится здесь.
YANDEX_VOICES = ["alena", "filipp", "madirus", "ermil", "zahar", "jane", "omazh"]
YANDEX_HIFI = ["nastya", "maxim", "dima", "zlata"]

YANDEX_TTS_URL = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"


class YandexEngine(_CloudTtsEngine):
    name = "yandex"
    sample_rate = 48000
    text_limit = 4900
    voices = YANDEX_VOICES + YANDEX_HIFI
    default_voice = "alena"
    hifi = set(YANDEX_HIFI)

    def _credentials(self) -> dict:
        from ..config import get_settings

        y = get_settings().tts.yandex
        if not (y.api_key and y.folder_id):
            raise CloudTtsError(
                "Не указаны api_key/folder_id Яндекса — заполните в настройках озвучки."
            )
        return {
            "api_key": y.api_key,
            "folder_id": y.folder_id,
            "emotion": y.emotion,
        }

    def _build_request(self, text, voice, creds):
        headers = {"Authorization": f"Api-Key {creds['api_key']}"}
        data = {
            "text": text,
            "lang": "ru-RU",
            "voice": voice,
            # lpcm = сырой int16 LE mono на sampleRateHertz — декодим без зависимостей.
            "format": "lpcm",
            "sampleRateHertz": "48000",
            "folderId": creds["folder_id"],
        }
        if creds.get("emotion") and creds["emotion"] != "neutral":
            data["emotion"] = creds["emotion"]
        return YANDEX_TTS_URL, headers, data, "lpcm"


_engine: YandexEngine | None = None
_lock = threading.Lock()


def get_engine(device: str = "cpu") -> YandexEngine:
    """Синглтон Яндекс-движка. device игнорируется (синтез в облаке)."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = YandexEngine()
        return _engine
