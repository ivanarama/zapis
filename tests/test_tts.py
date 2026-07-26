"""Юнит-тесты ядра озвучивания (без тяжёлых зависимостей: torch/av не нужны).

Запуск:  python tests\test_tts.py   (или через pytest, если установлен)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.tts import chapters, chunker, normalize  # noqa: E402


def test_split_chapters_detects_headings():
    text = "Глава 1\n\nПервый текст.\n\nГлава 2\n\nВторой текст."
    chs = chapters.split_chapters(text)
    assert len(chs) == 2, chs
    assert chs[0].title.startswith("Глава 1")
    assert "Первый текст" in chs[0].text
    assert "Второй текст" in chs[1].text


def test_split_chapters_fallback_single():
    text = "Просто текст без заголовков.\n\nЕщё абзац."
    chs = chapters.split_chapters(text)
    assert len(chs) == 1
    assert chs[0].title == "Книга"


def test_normalize_numbers_and_abbrev():
    out = normalize.normalize_text("В 1945 году, т.е. потом.")
    assert "1945" not in out, out  # число развёрнуто
    assert "то есть" in out, out
    # 1945 → «тысяча девятьсот сорок пять» (именительный, rule-based)
    assert "тысяч" in out or "девят" in out, out


def test_normalize_units_after_number():
    out = normalize.normalize_text("Это случилось в 1945 г.")
    assert "год" in out, out


def test_chunker_respects_limit_and_keeps_sentences():
    sentence = "Это предложение. " * 200  # ~3400 символов
    paragraphs = chunker.chunk_chapter(sentence, max_chars=200)
    assert paragraphs, "должен быть хотя бы один абзац"
    for para in paragraphs:
        for chunk in para:
            assert len(chunk) <= 200 or " " not in chunk, f"фрагмент слишком длинный: {len(chunk)}"


def test_chunker_splits_paragraphs():
    text = "Первый абзац, одно предложение.\n\nВторой абзац, другое предложение."
    paragraphs = chunker.chunk_chapter(text, max_chars=800)
    assert len(paragraphs) == 2, paragraphs


# ---------- облачные TTS-движки (базовый слой, без сети) ----------

from backend.tts.engine_cloud_base import decode_audio, split_text  # noqa: E402
from backend.tts.errors import CloudTtsError  # noqa: E402


def test_split_text_under_limit_returns_as_is():
    assert split_text("короткий текст", 100) == ["короткий текст"]


def test_split_text_respects_limit_and_keeps_sentences():
    s = "Предложение раз. " * 500  # ~9000 символов
    parts = split_text(s.strip(), 50)
    assert parts
    assert all(len(p) <= 50 for p in parts), [len(p) for p in parts]
    # ничего не потеряли и не удвоили
    joined = "".join(p.replace(" ", "") for p in parts)
    assert joined == s.strip().replace(" ", "")


def test_split_text_unsplittable_long_word_cut_hard():
    w = "а" * 120
    parts = split_text(w, 50)
    assert all(len(p) <= 50 for p in parts)
    assert "".join(parts) == w


def test_split_text_empty():
    assert split_text("", 50) == []
    assert split_text("   \n  ", 50) == []


def test_decode_lpcm_int16_to_float32():
    import numpy as np

    samples = np.array([0, 32767, -32768, 16384], dtype="<i2").tobytes()
    out = decode_audio(samples, "lpcm")
    assert out.dtype == np.float32
    assert abs(out[0]) < 1e-6
    assert abs(out[1] - 1.0) < 1e-4
    assert abs(out[2] + 1.0) < 1e-4
    assert abs(out[3] - 0.5) < 1e-4


def test_decode_empty_returns_empty():
    import numpy as np

    out = decode_audio(b"", "lpcm")
    assert out.dtype == np.float32
    assert out.shape == (0,)


def test_cloud_tts_error_is_runtime_error():
    assert issubclass(CloudTtsError, RuntimeError)


def test_pipeline_propagates_cloud_error():
    """Сбой облака (CloudTtsError) должен прерывать книгу, а не подменяться тишиной."""
    import asyncio
    import tempfile

    import backend.tts.pipeline as P

    class FakeEngine:
        accepts_accent_marks = False

        def initialize(self):
            pass

        def resolve_sample_rate(self, speaker, requested):
            return 48000

        def synth(self, *args, **kwargs):
            raise CloudTtsError("нет ключа")

    orig = P.get_engine
    P.get_engine = lambda *a, **k: FakeEngine()  # noqa: E731
    try:
        async def drive():
            agen = P.synthesize(
                text="Тест раз. Тест два.", out_dir=tempfile.mkdtemp(), engine_name="yandex"
            )
            async for _ in agen:
                pass

        raised = False
        try:
            asyncio.run(drive())
        except CloudTtsError:
            raised = True
        assert raised, "pipeline должен пробросить CloudTtsError, а не заглушить тишиной"
    finally:
        P.get_engine = orig


def test_edge_chunk_failure_threshold():
    """Транзиентные провалы → тишина (книга продолжается); N подряд → CloudTtsError."""
    import numpy as np

    from backend.tts.engine_edge import EdgeEngine

    e = EdgeEngine()
    # FAIL_THRESHOLD-1 провалов подряд — возвращаем тишину, без исключения.
    for _ in range(EdgeEngine.FAIL_THRESHOLD - 1):
        out = e._on_chunk_failure("транзиентный сбой")
        assert out.dtype == np.float32 and len(out) > 0, "должна вернуться тишина"
    # FAIL_THRESHOLD-й подряд — аборт.
    raised = False
    try:
        e._on_chunk_failure("ещё сбой")
    except CloudTtsError:
        raised = True
    assert raised, "после порога должен бросать CloudTtsError"


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
