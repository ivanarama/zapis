"""Тесты классификации сетевых ошибок при скачивании ASR-моделей.

Запуск:  python tests\test_asr_errors.py   (или через pytest, если установлен)
"""

import socket
import ssl
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.asr.base import (  # noqa: E402
    GIGAAM_CDN_HOST,
    HUGGINGFACE_HOST,
    describe_download_error,
)


def test_cert_error_blames_tls_interception():
    exc = ssl.SSLCertVerificationError("certificate verify failed: unable to get local issuer")
    msg = describe_download_error(exc, "модель Whisper", HUGGINGFACE_HOST)
    assert msg is not None
    assert "сертификат" in msg
    assert HUGGINGFACE_HOST in msg


def test_timeout_is_reported_as_timeout():
    msg = describe_download_error(socket.timeout("timed out"), "модель GigaAM", "cdn.example.ru")
    assert msg is not None and "таймаут" in msg


def test_http_status_is_surfaced():
    exc = urllib.error.HTTPError("https://cdn.example.ru/x", 403, "Forbidden", {}, None)
    msg = describe_download_error(exc, "модель GigaAM", "cdn.example.ru")
    assert msg is not None and "403" in msg


def test_httpx_style_response_status_is_surfaced():
    """huggingface_hub 1.x бросает httpx-ошибки — код лежит в .response."""

    class _Response:
        status_code = 502

    class _HttpxError(Exception):
        response = _Response()

    msg = describe_download_error(_HttpxError("bad gateway"), "модель Whisper", HUGGINGFACE_HOST)
    assert msg is not None and "502" in msg


def test_cause_chain_is_inspected():
    """Реальное исключение приходит обёрнутым (OSError <- SSLError)."""
    try:
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLError as inner:
            raise OSError("download failed") from inner
    except OSError as outer:
        msg = describe_download_error(outer, "модель GigaAM", "cdn.example.ru")
    assert msg is not None and "сертификат" in msg


def test_non_network_error_returns_none():
    """Нехватка памяти или битые веса не должны выдаваться за проблемы сети."""
    assert describe_download_error(MemoryError("out of memory"), "модель", "host") is None
    assert describe_download_error(ValueError("checksum mismatch"), "модель", "host") is None


def test_host_hint_names_the_sber_cdn():
    """Веса GigaAM качаются не с HuggingFace — в ошибке должен стоять свой хост."""
    assert "sberdevices" in GIGAAM_CDN_HOST


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  OK  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERR   {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
