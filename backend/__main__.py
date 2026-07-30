"""Запуск только бэкенда (без окна pywebview): python -m backend."""

# Как и в main.py: проверка TLS через системное хранилище сертификатов, иначе
# на машинах с корпоративным прокси не скачиваются модели с HuggingFace.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 — падать из-за сертификатов нельзя
    pass

from .main import run_server  # noqa: E402

if __name__ == "__main__":
    run_server()
