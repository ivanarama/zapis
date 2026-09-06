"""HTTP-слой диаризации: POST /api/asr/diarization/download.

Эндпоинт кнопки «Скачать модели» не исполнялся ни одним тестом: после первой
неудачи (не было сети) он обязан сбрасывать залипшую ошибку через reset_error()
и запускать initialize() в фоне, а при уже скачанных моделях — не делать
ничего. Отдельный файл: test_transcribe_contract.py — про /api/transcribe,
здесь — про жизнь диаризатора между транскрипциями.

Запуск:  python tests\\test_diarization_api.py   (или через pytest)
"""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _settings():
    """Герметичный срез настроек: эндпоинт читает только asr.diarization.

    Без подмены тест зависел бы от settings.json машины (включённая диаризация
    с реальной моделью эмбеддингов уводила бы в настоящий синглтон).
    """
    return SimpleNamespace(asr=SimpleNamespace(diarization=SimpleNamespace(
        embedding_model="wespeaker_en_voxceleb_resnet34_LM.onnx",
        num_threads=0,
        window_shift_ratio=0.3,
    )))


class _FakeDiarizer:
    """Диаризатор-двойник: считает сбросы ошибки и запуски initialize()."""

    def __init__(self, models_ready=False):
        self.ready = models_ready
        self.reset_calls = 0
        self.init_calls = 0
        self.initialized = threading.Event()

    def models_ready(self):
        return self.ready

    def reset_error(self):
        self.reset_calls += 1

    def initialize(self):
        self.init_calls += 1
        self.initialized.set()


def _patched(fake):
    """Подменяет настройки, доступность пакета и синглтон; возвращает restore.

    get_diarizer патчим на уровне модуля diarize — эндпоинт достаёт его
    оттуда в момент вызова (через asyncio.to_thread, но по ссылке).
    """
    import backend.main as m
    from backend.asr import diarize as d

    saved = (m.get_settings, d.is_available, d.get_diarizer)

    def restore():
        m.get_settings, d.is_available, d.get_diarizer = saved

    m.get_settings = lambda: _settings()
    d.is_available = lambda: True
    d.get_diarizer = lambda *a, **k: fake
    return restore


def test_download_without_package_returns_install_hint():
    """Пакета нет: 400 с подсказкой об установке, диаризатор не создаётся
    и ничего не качает."""
    import backend.main as m
    from backend.asr import diarize as d
    from fastapi.testclient import TestClient

    fake = _FakeDiarizer()
    restore = _patched(fake)
    d.is_available = lambda: False
    try:
        r = TestClient(m.app).post("/api/asr/diarization/download")
        assert r.status_code == 400
        assert "sherpa-onnx" in r.json()["error"]
        assert fake.init_calls == 0
    finally:
        restore()


def test_download_resets_stuck_error_and_starts_initialize():
    """Модели не скачаны: ответ downloading, залипшая ошибка сброшена
    (иначе повторный клик после первой неудачи делал бы ничего), initialize()
    реально стартовал в фоне."""
    import backend.main as m
    from fastapi.testclient import TestClient

    fake = _FakeDiarizer(models_ready=False)
    restore = _patched(fake)
    try:
        r = TestClient(m.app).post("/api/asr/diarization/download")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "downloading"}
        assert fake.reset_calls == 1, "перед повторной попыткой обязан быть reset_error"
        assert fake.initialized.wait(5), "initialize() должен стартовать в фоне"
    finally:
        restore()


def test_download_with_models_ready_shortcuts_to_ready():
    """Модели уже на диске: честный «ready», без сброса ошибки и без лишнего
    initialize() — повторный клик по кнопке не перезапускает загрузку."""
    import backend.main as m
    from fastapi.testclient import TestClient

    fake = _FakeDiarizer(models_ready=True)
    restore = _patched(fake)
    try:
        r = TestClient(m.app).post("/api/asr/diarization/download")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status": "ready"}
        assert fake.reset_calls == 0 and fake.init_calls == 0
    finally:
        restore()


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except Exception as e:  # noqa: BLE001 — один упавший тест не роняет прогон
            failed += 1
            print(f"FAIL  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
