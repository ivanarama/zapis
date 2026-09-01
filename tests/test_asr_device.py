"""Тесты выбора вычислительного устройства для ASR-движков.

Движки считают на разных стеках (GigaAM — torch, Whisper — CTranslate2),
поэтому и устройство у них разрешается независимо.

Запуск:  python tests\test_asr_device.py   (или через pytest, если установлен)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.asr import factory  # noqa: E402
from backend.schema import ASRSettings  # noqa: E402


def _reset():
    factory.set_device("auto", {})


def test_engine_device_defaults_to_common_setting():
    _reset()
    factory.set_device("cpu", {})
    assert factory._resolve_device("gigaam") == "cpu"
    assert factory._resolve_device("whisper") == "cpu"


def test_engine_override_wins_over_common():
    """Главный сценарий: whisper на видеокарте, GigaAM остаётся на CPU."""
    _reset()
    factory.set_device("cpu", {"whisper": "cuda"})
    assert factory._resolve_device("whisper") == "cuda"
    assert factory._resolve_device("gigaam") == "cpu"


def test_empty_override_falls_back_to_common():
    """None/пустая строка означают «наследовать», а не «cpu»."""
    _reset()
    factory.set_device("cuda", {"whisper": None, "gigaam": ""})
    assert factory._resolve_device("whisper") == "cuda"
    assert factory._resolve_device("gigaam") == "cuda"


def test_set_device_drops_cached_engines():
    """Устройство фиксируется при создании движка, поэтому кеш надо сбрасывать,
    иначе настройка не подействует до перезапуска."""
    _reset()
    factory.set_device("cpu", {})
    eng = factory.get_engine("whisper")
    assert factory._engines, "движок должен закешироваться"
    factory.set_device("cpu", {"whisper": "cuda"})
    assert not factory._engines, "смена устройства обязана сбросить кеш"
    assert factory.get_engine("whisper") is not eng


def test_same_device_keeps_loaded_engines():
    """Настройки сохраняются целиком при правке любого поля, и set_device
    зовётся на каждое сохранение. Если устройство не менялось, прогретую
    модель терять нельзя."""
    _reset()
    factory.set_device("cpu", {"whisper": "cuda"})
    eng = factory.get_engine("whisper")
    assert factory.set_device("cpu", {"whisper": "cuda"}) is False
    assert factory.get_engine("whisper") is eng, "движок пересоздали без причины"


def test_changed_device_reports_change():
    """А вот реальная смена обязана отчитаться — по этому признаку сервер
    решает, писать ли в лог, и пересоздаёт движки."""
    _reset()
    factory.set_device("cpu", {})
    assert factory.set_device("cuda", {}) is True
    assert factory.set_device("cuda", {"gigaam": "cpu"}) is True
    assert factory.set_device("cuda", {"gigaam": "cpu"}) is False


def test_empty_override_equals_absent_override():
    """None и пустая строка означают «наследовать» — это не изменение
    настройки, а тот же самый случай, что и отсутствующий ключ."""
    _reset()
    factory.set_device("cpu", {"whisper": "cuda"})
    assert factory.set_device("cpu", {"whisper": "cuda", "gigaam": None}) is False
    assert factory.set_device("cpu", {"whisper": "cuda", "gigaam": ""}) is False


def test_settings_schema_allows_per_engine_device():
    s = ASRSettings.model_validate({
        "device": "cpu",
        "whisper": {"model": "small", "device": "cuda"},
    })
    assert s.device == "cpu"
    assert s.whisper.device == "cuda"
    assert s.gigaam.device is None, "не заданное устройство движка = наследовать"


def test_settings_schema_rejects_unknown_device():
    try:
        ASRSettings.model_validate({"whisper": {"device": "rocm"}})
    except Exception:
        return
    raise AssertionError("неизвестное устройство должно отвергаться схемой")


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
