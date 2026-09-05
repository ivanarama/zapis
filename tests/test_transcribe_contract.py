"""Контракты вокруг /api/transcribe, которые не видит ни один другой тест.

Главный из них — «сбой диаризации не стоит пользователю расшифровки»:
_diarize_result обязан вернуть готовый текст в любом случае, а причину
неудачи — отдельным предупреждением. Регресс здесь не ловился ничем:
ни один тест не исполнял код main.py.

Запуск:  python tests\\test_transcribe_contract.py   (или через pytest)
"""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _settings(diarization_enabled):
    """Минимальная герметичная форма настроек для /api/transcribe.

    Без неё тесты зависели бы от реального settings.json: локальный файл
    с включённой диаризацией уводил бы эндпоинт в настоящий диаризатор.
    """
    return SimpleNamespace(asr=SimpleNamespace(
        engine="gigaam", language="ru", device="auto",
        gigaam=SimpleNamespace(device=None),
        whisper=SimpleNamespace(model="small", cpu_threads=4, device=None),
        diarization=SimpleNamespace(
            enabled=diarization_enabled, num_speakers=0, threshold=0.5,
            embedding_model="wespeaker_en_voxceleb_resnet34_LM.onnx",
            num_threads=0, window_shift_ratio=0.3,
        ),
    ))


class _OkEngine:
    name = "fake"

    def transcribe(self, data, filename, language="auto"):
        return {"text": "ок", "language": language,
                "segments": [{"start": 0.0, "end": 1.0, "text": "ок"}]}


def _patched(fake_diarizer):
    """Подменяет диаризатор, декодер аудио и настройки; возвращает
    (_diarize_result, restore).

    Импорты внутри _diarize_result выполняются в момент вызова, поэтому
    подмена атрибутов модулей работает без pytest-фикстур. Настройки патчим
    тоже: без этого тест зависел бы от реального settings.json машины.
    """
    import numpy as np

    import backend.main as m
    from backend.asr import base as asr_base
    from backend.asr import diarize as d
    from backend.main import _diarize_result

    saved_get = d.get_diarizer
    saved_decode = asr_base.decode_audio_bytes
    saved_settings = m.get_settings

    class _FakeDiarizer:
        def diarize(self, audio, num_speakers=0, threshold=0.5):
            if isinstance(fake_diarizer, Exception):
                raise fake_diarizer
            return fake_diarizer if fake_diarizer is not None else []

    def restore():
        d.get_diarizer = saved_get
        asr_base.decode_audio_bytes = saved_decode
        m.get_settings = saved_settings

    d.get_diarizer = lambda *a, **k: _FakeDiarizer()
    asr_base.decode_audio_bytes = lambda data, ext: np.zeros(16000, dtype=np.float32)
    m.get_settings = lambda: _settings(True)
    return _diarize_result, restore


def test_diarize_failure_keeps_transcript_with_warning():
    """Исключение диаризации не теряет расшифровку — она уже посчитана
    и стоила минут работы; причина уходит отдельным предупреждением."""
    fn, restore = _patched(RuntimeError("нет сети"))
    try:
        result = {"text": "готово", "language": "ru",
                  "segments": [{"start": 0.0, "end": 1.0, "text": "готово",
                                "words": [{"text": "готово", "start": 0.1, "end": 0.9}]}]}
        out, warning = fn(result, b"audio-bytes", "file.wav", None)
        assert out is result, "расшифровку обязаны вернуть ту же"
        assert warning and "нет сети" in warning
        assert "Расшифровка готова" in warning
    finally:
        restore()


def test_diarize_empty_turns_warns_not_silence():
    """Диаризатор не нашёл реплик: молча отдать текст без подписей нельзя —
    отсутствие подписей выглядит как баг, предупредаем словами."""
    fn, restore = _patched([])
    try:
        result = {"text": "текст", "language": "ru", "segments": []}
        out, warning = fn(result, b"audio-bytes", "file.wav", None)
        assert out is result
        assert warning and "не нашла реплик" in warning
    finally:
        restore()


def test_diarize_success_applies_speakers():
    """Успешная разметка пересобирает результат с подписями и без предупреждения."""
    fn, restore = _patched([{"start": 0.0, "end": 1.0, "speaker": 0}])
    try:
        result = {"text": "слово", "language": "ru",
                  "segments": [{"start": 0.0, "end": 1.0, "text": "слово",
                                "words": [{"text": "слово", "start": 0.2, "end": 0.8}]}]}
        out, warning = fn(result, b"audio-bytes", "file.wav", None)
        assert warning is None
        segs = out.get("segments") or []
        assert segs and segs[0].get("speaker") == 0, "подпись говорящего обязана появиться"
    finally:
        restore()


def test_second_transcribe_gets_409_while_first_runs():
    """Параллельная транскрипция после смены устройства загрузила бы вторую
    модель рядом с живой — второй запрос обязан получить 409. После
    завершения первой лок освобождается: следующий запрос снова 200."""
    import backend.main as m
    from fastapi.testclient import TestClient

    release = threading.Event()
    started = threading.Event()

    class _BlockingEngine:
        name = "fake"

        def transcribe(self, data, filename, language="auto"):
            started.set()
            # Guard убрали регрессом — тест умрёт быстро, а не через 10 секунд.
            release.wait(3)
            return {"text": "ok", "language": language, "segments": []}

    saved_settings, saved_engine = m.get_settings, m.asr_factory.get_engine
    m.get_settings = lambda: _settings(False)
    m.asr_factory.get_engine = lambda name=None: _BlockingEngine()
    try:
        results = {}

        def first():
            r = TestClient(m.app).post(
                "/api/transcribe", files={"file": ("a.wav", b"x")}
            )
            results["first"] = r.status_code

        t = threading.Thread(target=first, daemon=True)
        t.start()
        assert started.wait(10), "первая транскрипция не стартовала"
        results["second"] = TestClient(m.app).post(
            "/api/transcribe", files={"file": ("b.wav", b"x")}
        ).status_code
        release.set()
        t.join(10)
        assert results["first"] == 200
        assert results["second"] == 409, f"ожидали 409, получили {results}"

        # Лок обязан освобождаться finally-блоком: третья транскрипция
        # (release уже выставлен — движок не блокирует) снова проходит.
        results["third"] = TestClient(m.app).post(
            "/api/transcribe", files={"file": ("c.wav", b"x")}
        ).status_code
        assert results["third"] == 200, (
            f"лок не освободился после первой транскрипции: {results}"
        )
    finally:
        m.get_settings = saved_settings
        m.asr_factory.get_engine = saved_engine
        release.set()


def test_unavailable_package_skips_diarization_silently_when_not_explicit():
    """enabled=True в настройках, пакета нет, явного запроса не было (UI
    галочку не показывал и параметр не слал): расшифровка без подписей и
    БЕЗ предупреждения — причина уходит одним log.warning за запуск."""
    import backend.main as m
    from backend.asr import diarize as d
    from fastapi.testclient import TestClient

    saved_settings, saved_engine, saved_avail = (
        m.get_settings, m.asr_factory.get_engine, d.is_available,
    )
    m.get_settings = lambda: _settings(True)
    m.asr_factory.get_engine = lambda name=None: _OkEngine()
    d.is_available = lambda: False
    m._diarization_unavailable_logged = False  # глобальный флаг — сбрасываем
    try:
        r = TestClient(m.app).post(
            "/api/transcribe", files={"file": ("a.wav", b"x")}
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("warning") is None, "молчаливый пропуск — не warning в ответе"
        assert body["result"]["segments"][0].get("speaker") is None
    finally:
        m.get_settings = saved_settings
        m.asr_factory.get_engine = saved_engine
        d.is_available = saved_avail
        m._diarization_unavailable_logged = False


def test_speakers_param_alone_is_explicit_diarization_request():
    """speakers=N без diarize — явный запрос подписей: при недоступном пакете
    обязано быть честное предупреждение, а не молчание."""
    import backend.main as m
    from backend.asr import diarize as d
    from fastapi.testclient import TestClient

    _fn, restore = _patched(RuntimeError("пакет недоступен"))

    saved_settings, saved_engine, saved_avail = (
        m.get_settings, m.asr_factory.get_engine, d.is_available,
    )
    m.get_settings = lambda: _settings(False)  # enabled=false!
    m.asr_factory.get_engine = lambda name=None: _OkEngine()
    d.is_available = lambda: False
    try:
        r = TestClient(m.app).post(
            "/api/transcribe",
            files={"file": ("a.wav", b"x")},
            params={"speakers": "2"},
        )
        assert r.status_code == 200
        warning = r.json().get("warning") or ""
        assert "Расшифровка готова" in warning
        assert "пакет недоступен" in warning
    finally:
        restore()
        m.get_settings = saved_settings
        m.asr_factory.get_engine = saved_engine
        d.is_available = saved_avail


def test_explicit_diarize_with_unavailable_package_gets_honest_warning():
    """Явный diarize=true при недоступном пакете — честное предупреждение:
    вызывающий просил подписи и обязан узнать, почему их нет."""
    import backend.main as m
    from backend.asr import diarize as d
    from fastapi.testclient import TestClient

    _fn, restore = _patched(RuntimeError("пакет недоступен"))

    saved_settings, saved_engine, saved_avail = (
        m.get_settings, m.asr_factory.get_engine, d.is_available,
    )
    m.get_settings = lambda: _settings(True)
    m.asr_factory.get_engine = lambda name=None: _OkEngine()
    d.is_available = lambda: False
    try:
        r = TestClient(m.app).post(
            "/api/transcribe",
            files={"file": ("a.wav", b"x")},
            params={"diarize": "true"},  # фронт шлёт query-строкой, не формой
        )
        assert r.status_code == 200
        warning = r.json().get("warning") or ""
        assert "Расшифровка готова" in warning
        assert "пакет недоступен" in warning
    finally:
        restore()
        m.get_settings = saved_settings
        m.asr_factory.get_engine = saved_engine
        d.is_available = saved_avail


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
