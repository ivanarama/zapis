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


def test_set_device_frees_models_of_cached_engines():
    """Сброс кеша не бросает прогретые модели на милость сборщика мусора:
    движок обязан явно освободить модель до пересоздания, иначе в момент
    загрузки новой на другом устройстве обе оказываются в памяти
    (small→large-v3 — лишние полтора гигабайта в пике на машинах с 8 ГБ)."""
    _reset()
    factory.set_device("cpu", {})
    factory.get_engine("whisper")
    eng = factory.get_engine("whisper")
    freed = []
    eng.unload = lambda: freed.append(True)  # type: ignore[method-assign]
    factory.set_device("cuda", {})
    assert freed, "set_device обязан звать unload у кешированных движков"


# ---------- Откаты cuda→cpu внутри движков ----------
#
# GigaAM считает на torch (в сборке без CUDA падает уже после скачивания
# весов — откат заранее), Whisper на CTranslate2 (отката нет — ошибка с
# подсказкой). Оба пути гоняются по настоящему initialize(), наружу
# подменяются только torch-совместимый стаб и модуль движка.


class _FakeDevice:
    """Минимальная замена torch.device: атрибут .type и человекочитаемый str."""

    def __init__(self, spec):
        self.type = spec

    def __str__(self):
        return self.type


def _fake_torch(cuda_available):
    import types

    return types.SimpleNamespace(
        device=_FakeDevice,
        cuda=types.SimpleNamespace(
            is_available=lambda: cuda_available,
            empty_cache=lambda: None,
        ),
    )


def _with_patched_modules(patches, fn):
    """Ставит пары имя→модуль в sys.modules, зовёт fn, возвращает как было."""
    import sys

    saved = {name: sys.modules.get(name) for name in patches}
    sys.modules.update(patches)
    try:
        return fn()
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def test_gigaam_requested_cuda_falls_back_to_cpu_without_error():
    """GigaAM просил cuda, а torch без CUDA: тихий откат на CPU — модель
    создаётся, ошибки в статусе нет. Падение «Torch not compiled with CUDA»
    после скачивания весов недопустимо, молчаливое падение движка — тем более."""
    import sys
    import types

    created = {}

    class _FakeModel:
        def __init__(self, version, device="cpu", fp16=False):
            created["device"] = device

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.hf_hub_download = lambda *a, **k: "kenlm.bin"

    def _run():
        from backend.asr import gigaam_engine as ge

        saved_attrs = (
            ge.torch, ge.GigaAMCTC, ge.LongformCTC, ge.CTCDecoderWithLM,
            ge._check_v3_available,
        )
        ge.torch = sys.modules["torch"]
        ge.GigaAMCTC = _FakeModel
        ge.LongformCTC = lambda model, segment_shift=0: None
        ge.CTCDecoderWithLM = lambda longform, kenlm_path: None
        ge._check_v3_available = lambda version: True
        try:
            eng = ge.GigaamEngine(version="v3", device="cuda")
            eng.initialize()
            st = eng.get_status()
            assert st["status"] == "ready", st
            assert created["device"] == "cpu", "модель обязана уехать на CPU"
            assert eng._error is None, "тихий откат — не ошибка"
        finally:
            (ge.torch, ge.GigaAMCTC, ge.LongformCTC,
             ge.CTCDecoderWithLM, ge._check_v3_available) = saved_attrs
            # Модуль мог импортироваться со стабом torch — не оставлять его в кеше
            sys.modules.pop("backend.asr.gigaam_engine", None)

    _with_patched_modules(
        {"torch": _fake_torch(False), "huggingface_hub": fake_hub}, _run,
    )


def test_gigaam_requested_cuda_used_when_available():
    """А если CUDA есть — откатывать нельзя: модель остаётся на видеокарте."""
    import sys
    import types

    created = {}

    class _FakeModel:
        def __init__(self, version, device="cpu", fp16=False):
            created["device"] = device

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.hf_hub_download = lambda *a, **k: "kenlm.bin"

    def _run():
        from backend.asr import gigaam_engine as ge

        saved_attrs = (
            ge.torch, ge.GigaAMCTC, ge.LongformCTC, ge.CTCDecoderWithLM,
            ge._check_v3_available,
        )
        ge.torch = sys.modules["torch"]
        ge.GigaAMCTC = _FakeModel
        ge.LongformCTC = lambda model, segment_shift=0: None
        ge.CTCDecoderWithLM = lambda longform, kenlm_path: None
        ge._check_v3_available = lambda version: True
        try:
            eng = ge.GigaamEngine(version="v3", device="cuda")
            eng.initialize()
            assert eng.get_status()["status"] == "ready"
            assert created["device"] == "cuda", created
        finally:
            (ge.torch, ge.GigaAMCTC, ge.LongformCTC,
             ge.CTCDecoderWithLM, ge._check_v3_available) = saved_attrs
            sys.modules.pop("backend.asr.gigaam_engine", None)

    _with_patched_modules(
        {"torch": _fake_torch(True), "huggingface_hub": fake_hub}, _run,
    )


def test_whisper_requested_cuda_failure_sets_actionable_error():
    """У Whisper отката нет: видеокарту попросили, а она не завелась — статус
    error с подсказкой про cuDNN и способом вернуться на CPU, а не голый
    «DLL load failed»."""
    import sys
    import types

    from backend.asr import whisper_engine as we

    class _BrokenWhisperModel:
        def __init__(self, *a, **k):
            raise RuntimeError("DLL load failed while importing")

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = _BrokenWhisperModel

    def _run():
        eng = we.WhisperEngine(model_size="tiny", device="cuda")
        eng.initialize()
        st = eng.get_status()
        assert st["status"] == "error", st
        assert "cuDNN" in st["error"], st["error"]
        assert 'device = "cpu"' in st["error"], st["error"]

    _with_patched_modules({"faster_whisper": fake_fw}, _run)


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
