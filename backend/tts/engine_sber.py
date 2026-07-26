"""Сбер SaluteSpeech TTS-движок: облачный синтез русской речи.

Альтернатива Яндексу — каталог голосов Сбера. Авторизация: OAuth2 client_credentials
(client_id + client_secret из дев-портала developers.sber.ru → access-token ~24ч,
кэшируется). Синтез: POST smartspeech.sber.ru/rest/v1/text:synthesize?format=wav16
&voice=..., Bearer-токен, текст в теле → WAV 16 кГц (декодим через soundfile).

ВАЖНО (ограничение провайдера): API Сбера обслуживается сертификатом российского
центра доверия Минцифры. Стандартный certifi его не знает → SSL-рукопожатие упадёт,
пока корневой сертификат Минцифры не установлен в систему (или не подставлен через
SSL_CERT_FILE / REQUESTS_CA_BUNDLE). Это не баг приложения. Для «из коробки» без
возни с сертификатами используйте Яндекс SpeechKit (стандартные CA).
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from urllib.parse import urlencode

import httpx

from .engine_cloud_base import _CloudTtsEngine
from .errors import CloudTtsError

log = logging.getLogger("zavuk.tts.engine_sber")

SBER_TOKEN_URL = "https://salutedevices.sber.ru/api/oauth/token"
SBER_TTS_URL = "https://smartspeech.sber.ru/rest/v1/text:synthesize"
# SALUTE_SPEECH_PERSONAL — для физлиц. Для юрлиц поменяйте на SALUTE_SPEECH_BUSINESS.
SBER_SCOPE = "SALUTE_SPEECH_PERSONAL"

# Каталог голосов SaluteSpeech (кураторский список; точные имена сверяйте с актуальной
# докой Сбера — каталог меняется). По умолчанию Nazar (мужской, нейтральный).
SBER_VOICES = ["Nazar", "Nikola", "Kira", "Tatiana", "Prohor", "Sasha"]


class SberEngine(_CloudTtsEngine):
    name = "sber"
    sample_rate = 16000  # wav16 = 16 кГц; pipeline перекодирует в выходной формат книги
    text_limit = 1000  # лимит Sync-синтеза Сбера на запрос; дробим по предложениям
    voices = SBER_VOICES
    default_voice = "Nazar"

    def __init__(self):
        self._access: str | None = None
        self._access_exp: float = 0.0
        self._lock = threading.Lock()

    def _credentials(self) -> dict:
        from ..config import get_settings

        s = get_settings().tts.sber
        if not (s.client_id and s.client_secret):
            raise CloudTtsError(
                "Не указаны client_id/client_secret Сбера — заполните в настройках озвучки."
            )
        return {"client_id": s.client_id, "client_secret": s.client_secret}

    def _get_access(self, creds: dict) -> str:
        """Возвращает access-token (живёт ~24ч), кэширует с запасом 5 мин."""
        with self._lock:
            if self._access and time.time() < self._access_exp - 300:
                return self._access
            basic = base64.b64encode(
                f"{creds['client_id']}:{creds['client_secret']}".encode()
            ).decode()
            try:
                with httpx.Client(timeout=30.0) as c:
                    r = c.post(
                        SBER_TOKEN_URL,
                        headers={
                            "Authorization": f"Basic {basic}",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                        data={"grant_type": "client_credentials", "scope": SBER_SCOPE},
                    )
            except (httpx.HTTPError, OSError) as e:
                hint = (
                    "Возможно, нужен сертификат Минцифры (SSL)." if "SSL" in str(e) else ""
                )
                raise CloudTtsError(f"Не удалось получить access-token Сбера: {e} {hint}") from e
            if r.status_code in (401, 403):
                raise CloudTtsError("Сбер: неверные client_id/client_secret.")
            if r.status_code >= 400:
                raise CloudTtsError(
                    f"Сбер: ошибка авторизации (HTTP {r.status_code}): {r.text[:200]}"
                )
            try:
                tok = r.json()
            except ValueError as e:
                raise CloudTtsError(f"Сбер: некорректный ответ авторизации: {e}") from e
            self._access = tok.get("access_token")
            self._access_exp = time.time() + float(tok.get("expires_in", 86400))
            if not self._access:
                raise CloudTtsError("Сбер: пустой access_token в ответе.")
            log.info("Получен access-token Сбера (живёт %ss).", tok.get("expires_in"))
            return self._access

    def _build_request(self, text, voice, creds):
        access = self._get_access(creds)
        # Параметры кодируем в URL — базовый _post отправляет тело как есть.
        url = SBER_TTS_URL + "?" + urlencode({"format": "wav16", "voice": voice})
        headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/text"}
        return url, headers, text, "wav"


_engine: SberEngine | None = None
_lock = threading.Lock()


def get_engine(device: str = "cpu") -> SberEngine:
    """Синглтон Сбер-движка. device игнорируется (синтез в облаке)."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = SberEngine()
        return _engine
