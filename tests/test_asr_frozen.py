"""Тесты поведения ASR внутри собранного приложения (PyInstaller, sys.frozen).

В дистрибутиве sys.executable — это сам Zapis.exe, а не Python, поэтому любой
вызов «sys.executable -m pip …» запускает вторую копию приложения вместо
установки пакета. Тесты держат этот класс ошибок закрытым.

Запуск:  python tests\test_asr_frozen.py   (или через pytest, если установлен)
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.asr.gigaam_engine import CTCDecoderWithLM, GigaamEngine  # noqa: E402


class _Frozen:
    """Притворяется собранным приложением и глушит запуск процессов."""

    def __enter__(self):
        self.calls = []
        self._saved_kenlm = sys.modules.get("kenlm", "<absent>")
        self._saved_check_call = subprocess.check_call
        # None в sys.modules заставляет `import kenlm` бросить ImportError —
        # так воспроизводится машина без Visual C++ Runtime.
        sys.modules["kenlm"] = None
        subprocess.check_call = lambda *a, **kw: self.calls.append(a)
        sys.frozen = True
        return self

    def __exit__(self, *exc):
        del sys.frozen
        subprocess.check_call = self._saved_check_call
        if self._saved_kenlm == "<absent>":
            sys.modules.pop("kenlm", None)
        else:
            sys.modules["kenlm"] = self._saved_kenlm
        return False


def test_kenlm_autoinstall_never_runs_in_frozen_build():
    """Главный сценарий: kenlm не импортируется в дистрибутиве. Раньше это
    запускало вторую копию приложения и подвешивало первую в check_call."""
    with _Frozen() as f:
        assert CTCDecoderWithLM._ensure_kenlm() is False
        assert not f.calls, f"в собранном приложении процессы не запускаем: {f.calls}"


def test_kenlm_autoinstall_still_tried_from_source():
    """Из исходников поведение прежнее: pip зовём (здесь — заглушкой)."""
    saved = sys.modules.get("kenlm", "<absent>")
    saved_call = subprocess.check_call
    calls = []
    sys.modules["kenlm"] = None
    subprocess.check_call = lambda *a, **kw: calls.append(a)
    try:
        # Установка «удастся», но повторный import снова упрётся в None —
        # функция честно вернёт False, не соврав про наличие модели.
        assert CTCDecoderWithLM._ensure_kenlm() is False
        assert calls, "вне сборки установку пробовать надо"
        assert "pip" in calls[0][0], calls[0]
    finally:
        subprocess.check_call = saved_call
        if saved == "<absent>":
            sys.modules.pop("kenlm", None)
        else:
            sys.modules["kenlm"] = saved


def test_gigaam_install_is_refused_in_frozen_build():
    """Установка пакета gigaam из UI в дистрибутиве невозможна — движок обязан
    объяснить это текстом, а не пытаться дёрнуть pip."""
    with _Frozen() as f:
        eng = GigaamEngine.__new__(GigaamEngine)  # без загрузки моделей
        eng._needs_install = True
        eng._error = None
        eng.install_and_init()
        assert eng._needs_install is False
        assert eng._error and "pip install" in eng._error
        assert not f.calls, "pip в собранном приложении не зовём"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
