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
