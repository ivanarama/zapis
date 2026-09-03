"""Тесты склейки расшифровки с разметкой говорящих.

Модели тут не нужны: проверяем чистую логику — сопоставление слов с репликами,
нарезку сегментов по смене говорящего и подписи в экспорте.

Запуск:  python tests\test_diarize.py   (или через pytest, если установлен)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.formats import (  # noqa: E402
    apply_speakers,
    assign_speakers,
    format_result,
    format_srt,
    format_txt,
)


def _w(text, start, end):
    return {"text": text, "start": start, "end": end}


def _turn(start, end, speaker):
    return {"start": start, "end": end, "speaker": speaker}


def test_word_takes_speaker_with_max_overlap():
    words = [_w("привет", 0.0, 0.5), _w("да", 5.0, 5.4)]
    turns = [_turn(0.0, 1.0, 0), _turn(4.8, 6.0, 1)]
    assign_speakers(words, turns)
    assert words[0]["speaker"] == 0
    assert words[1]["speaker"] == 1


def test_word_on_boundary_goes_to_bigger_overlap():
    """Слово наполовину в чужой реплике достаётся тому, кто перекрывает больше."""
    words = [_w("слово", 1.0, 2.0)]
    turns = [_turn(0.0, 1.2, 0), _turn(1.2, 3.0, 1)]
    assign_speakers(words, turns)
    assert words[0]["speaker"] == 1


def test_word_outside_all_turns_inherits_nearest():
    """Диаризатор считает часть звука паузой; слово не должно остаться без
    говорящего — берём ближайшего по времени соседа, а не предыдущего."""
    words = [_w("раз", 0.0, 0.5), _w("два", 3.4, 3.6), _w("три", 4.0, 4.5)]
    turns = [_turn(0.0, 1.0, 0), _turn(3.9, 5.0, 1)]
    assign_speakers(words, turns)
    assert words[0]["speaker"] == 0
    # «два» ближе к началу реплики второго (0.3 с) чем к концу первой (2.4 с)
    assert words[1]["speaker"] == 1
    assert words[2]["speaker"] == 1


def test_zero_length_word_still_gets_speaker():
    """У Whisper попадаются слова с start == end — нулевое перекрытие не должно
    оставлять их без подписи."""
    words = [_w("ага", 2.0, 2.0)]
    turns = [_turn(1.0, 3.0, 0)]
    assign_speakers(words, turns)
    assert words[0]["speaker"] == 0


def test_segments_split_on_speaker_change_without_pause():
    """Перебивают друг друга без пауз: сегмент обязан разрезаться всё равно."""
    words = [_w("привет", 0.0, 0.4), _w("здравствуйте", 0.45, 1.2)]
    turns = [_turn(0.0, 0.42, 0), _turn(0.43, 1.5, 1)]
    result = format_result(words, turns=turns)
    assert len(result["segments"]) == 2
    assert result["segments"][0]["speaker"] == 0
    assert result["segments"][1]["speaker"] == 1
    assert result["speakers"] == 2


def test_no_turns_keeps_old_behaviour():
    """Без диаризации результат должен быть в точности как раньше: ни ключа
    speaker в сегментах, ни speakers в корне."""
    words = [_w("раз", 0.0, 0.4), _w("два", 0.45, 0.9)]
    result = format_result(words)
    assert len(result["segments"]) == 1
    assert "speaker" not in result["segments"][0]
    assert "speakers" not in result
    assert result["text"] == "раз два"


def test_apply_speakers_rebuilds_existing_result():
    """Диаризация применяется к уже готовой расшифровке — слова берутся из
    сегментов, интерфейс ASR-движков при этом не меняется."""
    plain = format_result([_w("раз", 0.0, 0.4), _w("два", 0.5, 0.9)])
    assert len(plain["segments"]) == 1
    diarized = apply_speakers(plain, [_turn(0.0, 0.45, 0), _turn(0.46, 1.0, 1)])
    assert len(diarized["segments"]) == 2
    assert [s["speaker"] for s in diarized["segments"]] == [0, 1]


def test_apply_speakers_without_turns_returns_input():
    plain = format_result([_w("раз", 0.0, 0.4)])
    assert apply_speakers(plain, []) is plain


def test_txt_export_groups_consecutive_segments_of_one_speaker():
    words = [
        _w("раз", 0.0, 0.4),
        _w("два", 2.0, 2.4),      # пауза рвёт сегмент, но говорящий тот же
        _w("ответ", 5.0, 5.5),
    ]
    turns = [_turn(0.0, 3.0, 0), _turn(4.9, 6.0, 1)]
    result = format_result(words, turns=turns)
    txt = format_txt(result)
    assert txt == "Спикер 1: раз два\n\nСпикер 2: ответ", txt
    # Тот же текст уходит в копирование и в LLM
    assert result["text"] == txt


def test_srt_export_prefixes_speaker():
    words = [_w("раз", 0.0, 0.4), _w("два", 5.0, 5.4)]
    turns = [_turn(0.0, 1.0, 0), _turn(4.9, 6.0, 1)]
    srt = format_srt(format_result(words, turns=turns))
    assert "Спикер 1: раз" in srt
    assert "Спикер 2: два" in srt


def test_speakers_are_renumbered_by_first_appearance():
    """Кластеры нумеруются произвольно; пользователь ждёт, что первый
    заговоривший — «Спикер 1»."""
    from backend.asr.diarize import renumber_by_first_appearance

    turns = [_turn(0.0, 1.0, 7), _turn(1.0, 2.0, 3), _turn(2.0, 3.0, 7)]
    out = renumber_by_first_appearance(turns)
    assert [t["speaker"] for t in out] == [0, 1, 0]


def test_reset_error_allows_retry_after_failed_download():
    """Без сброса ошибки кнопка «Скачать модели» после первой неудачи
    (не было сети) не делала бы ничего до перезапуска приложения."""
    from backend.asr import diarize as d

    eng = d.Diarizer()
    eng._error = "нет сети"
    eng.reset_error()
    assert eng._error is None
    assert eng.get_status()["status"] != "error"


def test_unload_returns_immediately_while_busy():
    """Выгрузку зовёт обработчик настроек из event loop. Если прямо сейчас
    идёт разметка, она обязана вернуться сразу — иначе подвиснет весь UI."""
    import threading
    import time

    from backend.asr import diarize as d

    eng = d.Diarizer()
    eng._sd = object()  # как будто модели загружены

    holding = threading.Event()
    release = threading.Event()

    def worker():
        with eng._lock:       # имитируем идущую разметку в рабочем потоке
            holding.set()
            release.wait(5)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    holding.wait(5)
    try:
        started = time.perf_counter()
        eng.unload()
        elapsed = time.perf_counter() - started
        assert elapsed < 0.5, f"unload ждал {elapsed:.2f} c"
        assert eng._sd is not None, "модели заняты — трогать их нельзя"
    finally:
        release.set()
        t.join(5)

    # Освободился — теперь выгрузка проходит
    eng.unload()
    assert eng._sd is None


def test_diarizer_reports_missing_package_instead_of_crashing():
    """Если sherpa-onnx не установлен, initialize() обязан поймать ImportError
    и выставить понятный _error, а не уронить транскрибацию импортом."""
    from backend.asr import diarize as d

    # None в sys.modules заставляет `import sherpa_onnx` поднять ImportError —
    # тот же паттерн, что sys.modules['kenlm'] = None в test_asr_frozen.py.
    saved = sys.modules.get("sherpa_onnx")
    sys.modules["sherpa_onnx"] = None
    try:
        eng = d.Diarizer()
        eng.initialize()
    finally:
        if saved is None:
            sys.modules.pop("sherpa_onnx", None)
        else:
            sys.modules["sherpa_onnx"] = saved
    st = eng.get_status()
    assert st["status"] == "error"
    assert "sherpa-onnx" in st["error"]


def test_unsorted_words_get_correct_speakers():
    """faster-whisper изредка выдаёт сегмент с таймкодами «в прошлое»: слово,
    пришедшее раньше предыдущего, обязано получить своего говорящего, а не
    чужого от ближайшего соседа (указатель base уже прошёл его реплику)."""
    words = [_w("позднее", 5.0, 5.4), _w("раннее", 0.0, 0.5)]
    turns = [_turn(0.0, 1.0, 0), _turn(4.8, 6.0, 1)]
    assign_speakers(words, turns)
    by_text = {w["text"]: w["speaker"] for w in words}
    assert by_text["раннее"] == 0
    assert by_text["позднее"] == 1


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
