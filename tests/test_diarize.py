"""Тесты диаризации: склейка с расшифровкой и сам Diarizer.

Модели тут не нужны: склейка — чистая логика (сопоставление слов с репликами,
нарезка сегментов по смене говорящего, подписи в экспорте), а Diarizer гоняется
по настоящему initialize() со стабом sherpa-onnx в sys.modules.

Запуск:  python tests\test_diarize.py   (или через pytest, если установлен)
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.asr.base import SAMPLE_RATE  # noqa: E402
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


# ---------- Diarizer: initialize() и diarize() со стабом sherpa-onnx ----------
#
# sherpa-onnx в тестовом окружении не установлен, и стаб в sys.modules проходит
# по настоящему пути импорта внутри initialize(). Наружу торчат только модуль
# пакета и download_models (сеть): сборка конфига, validate и создание
# распознавателя идут по реальному коду.


def _fake_sherpa_module(segments=(), sample_rate=SAMPLE_RATE):
    """Собирает модуль-подмену с сигнатурами, которые использует diarize.py.

    Возвращает (модуль, журнал вызовов): параметры кластеризации и факт запуска
    process проверяются по журналу, без ручной установки приватных полей.
    """
    import types

    calls = SimpleNamespace(set_config=0, process=[], sd=None)

    class FastClusteringConfig:
        def __init__(self, num_clusters=-1, threshold=0.5):
            self.num_clusters = num_clusters
            self.threshold = threshold

    class OfflineSpeakerSegmentationPyannoteModelConfig:
        def __init__(self, model="", window_shift_ratio=0.1):
            self.model = model
            self.window_shift_ratio = window_shift_ratio

    class OfflineSpeakerSegmentationModelConfig:
        def __init__(self, pyannote=None, num_threads=0):
            self.pyannote = pyannote
            self.num_threads = num_threads

    class SpeakerEmbeddingExtractorConfig:
        def __init__(self, model="", num_threads=0):
            self.model = model
            self.num_threads = num_threads

    class OfflineSpeakerDiarizationConfig:
        def __init__(self, segmentation=None, embedding=None, clustering=None,
                     min_duration_on=0.3, min_duration_off=0.5):
            self.segmentation = segmentation
            self.embedding = embedding
            self.clustering = clustering
            self.min_duration_on = min_duration_on
            self.min_duration_off = min_duration_off

        def validate(self):
            return True

    class Segment:
        def __init__(self, start, end, speaker):
            self.start, self.end, self.speaker = start, end, speaker

    class ProcessResult:
        def __init__(self, items):
            self._items = list(items)

        def sort_by_start_time(self):
            return sorted(self._items, key=lambda s: s.start)

    class OfflineSpeakerDiarization:
        def __init__(self, config):
            self.config = config
            self.sample_rate = sample_rate
            self.segments = [Segment(*s) for s in segments]
            calls.sd = self

        def set_config(self, config):
            calls.set_config += 1
            self.config = config

        def process(self, audio, callback=None):
            calls.process.append((audio, callback))
            if callback is not None:
                callback(2, 2)  # две секции из двух — прогресс 1.0
            return ProcessResult(self.segments)

    mod = types.ModuleType("sherpa_onnx")
    for cls in (
        FastClusteringConfig,
        OfflineSpeakerSegmentationPyannoteModelConfig,
        OfflineSpeakerSegmentationModelConfig,
        SpeakerEmbeddingExtractorConfig,
        OfflineSpeakerDiarizationConfig,
        OfflineSpeakerDiarization,
    ):
        setattr(mod, cls.__name__, cls)
    return mod, calls


@contextmanager
def _fake_sherpa_installed(segments=(), sample_rate=SAMPLE_RATE):
    """Ставит стаб sherpa_onnx в sys.modules, по выходе возвращает как было."""
    mod, calls = _fake_sherpa_module(segments, sample_rate)
    saved = sys.modules.get("sherpa_onnx")
    sys.modules["sherpa_onnx"] = mod
    try:
        yield calls
    finally:
        if saved is None:
            sys.modules.pop("sherpa_onnx", None)
        else:
            sys.modules["sherpa_onnx"] = saved


@contextmanager
def _loaded_diarizer(segments=(), sample_rate=SAMPLE_RATE):
    """Diarizer с «загруженными» моделями: initialize() настоящий, качать
    нечего (download_models — no-op), sherpa-onnx — стаб."""
    from backend.asr import diarize as d

    with _fake_sherpa_installed(segments, sample_rate) as calls:
        eng = d.Diarizer()
        eng.download_models = lambda: None
        eng.initialize()
        yield eng, calls


def test_status_lifecycle_via_real_initialize():
    """Статусы не ставятся вручную: initialize() → ready, unload() → idle,
    снова initialize() → ready — весь путь по реальному коду."""
    with _loaded_diarizer() as (eng, calls):
        st = eng.get_status()
        assert st["status"] == "ready", st
        assert "pyannote-segmentation-3.0" in st["detail"]
        assert "resnet34" in st["detail"], "в detail должна быть модель эмбеддингов"

        eng.unload()
        st = eng.get_status()
        assert st["status"] == "idle", st

        eng.initialize()
        assert eng.get_status()["status"] == "ready"


def test_failed_download_names_github_and_sticks():
    """Сеть недоступна: статус error с именем хоста (модели лежат на GitHub,
    третьем по счёту), повторный initialize() молча выходит — шторма попыток
    нет, ошибка залипает до reset_error()."""
    import urllib.error

    from backend.asr import diarize as d

    attempts = []
    with _fake_sherpa_installed() as calls:
        eng = d.Diarizer()

        def _no_network():
            attempts.append(1)
            raise urllib.error.URLError("нет соединения")

        eng.download_models = _no_network
        eng.initialize()
        st = eng.get_status()
        assert st["status"] == "error", st
        assert "github.com" in st["error"], st["error"]
        assert "модели диаризации" in st["error"], st["error"]

        eng.initialize()
        assert len(attempts) == 1, "ошибка должна удерживать повторные initialize()"


def test_reset_error_allows_real_retry_after_failed_download():
    """Кнопка «Скачать модели» после первой неудачи: reset_error() + initialize()
    обязаны повторить попытку по настоящему пути и дойти до готовности."""
    import urllib.error

    from backend.asr import diarize as d

    with _fake_sherpa_installed() as calls:
        eng = d.Diarizer()
        network_down = [True]

        def _flaky_download():
            if network_down[0]:
                network_down[0] = False
                raise urllib.error.URLError("нет соединения")

        eng.download_models = _flaky_download
        eng.initialize()
        assert eng.get_status()["status"] == "error"

        eng.reset_error()
        eng.initialize()
        assert eng.get_status()["status"] == "ready"


def test_diarize_short_audio_returns_empty_without_touching_models():
    """Аудио короче секунды делить некого: пустой список, и модели даже не
    запускаются — процесс на длинной записи дорог, лишний вызов не нужен."""
    import numpy as np

    with _loaded_diarizer(segments=[(0.0, 1.0, 0)]) as (eng, calls):
        assert eng.diarize(np.zeros(8000, dtype=np.float32)) == []
        assert calls.process == [], "process не должен зваться для короткого аудио"


def test_diarize_raises_on_sample_rate_mismatch():
    """Модель ждёт свою частоту: несовпадение с декодером — понятная ошибка
    с обеими частотами, а не тихая чушь в разметке."""
    import numpy as np

    with _loaded_diarizer(sample_rate=8000) as (eng, calls):
        try:
            eng.diarize(np.zeros(16000, dtype=np.float32))
        except RuntimeError as e:
            assert "8000" in str(e) and "16000" in str(e), str(e)
        else:
            raise AssertionError("ожидали RuntimeError при несовпадении частоты")
        assert calls.process == []


def test_diarize_passes_num_speakers_and_threshold_to_clustering():
    """num_speakers/threshold живут в FastClusteringConfig и обновляются на
    каждый запуск через set_config — без перезагрузки моделей."""
    import numpy as np

    pcm = np.zeros(32000, dtype=np.float32)
    with _loaded_diarizer(segments=[(0.0, 1.0, 0)]) as (eng, calls):
        eng.diarize(pcm, num_speakers=2, threshold=0.4)
        cfg = calls.sd.config.clustering
        assert cfg.num_clusters == 2 and cfg.threshold == 0.4
        assert calls.set_config == 1
        assert calls.process[0][0].dtype == np.float32, "в sherpa уходит float32"

        # Повторный запуск без явного числа — авто-кластеризация (num_clusters=-1)
        eng.diarize(pcm, threshold=0.6)
        cfg = calls.sd.config.clustering
        assert cfg.num_clusters == -1 and cfg.threshold == 0.6
        assert calls.set_config == 2


def test_diarize_sorts_turns_and_renumbers_speakers():
    """sherpa отдаёт сегменты в своём порядке с произвольными номерами
    кластеров: наружу — сортировка по времени и перенумерация по первому
    появлению."""
    import numpy as np

    segments = [(5.0, 6.0, 3), (1.0, 2.0, 3), (0.0, 1.0, 7)]
    with _loaded_diarizer(segments=segments) as (eng, calls):
        turns = eng.diarize(np.zeros(32000, dtype=np.float32))
        assert [(t["start"], t["end"], t["speaker"]) for t in turns] == [
            (0.0, 1.0, 0), (1.0, 2.0, 1), (5.0, 6.0, 1),
        ], turns


def test_diarize_progress_gets_fractions_and_survives_exceptions():
    """Прогресс приходит долями (0..1); падение колбэка UI не роняет разметку.
    Колбэк уходит в sherpa только когда он вообще нужен."""
    import numpy as np

    pcm = np.zeros(32000, dtype=np.float32)
    with _loaded_diarizer(segments=[(0.0, 1.0, 0)]) as (eng, calls):
        seen = []
        turns = eng.diarize(pcm, progress=seen.append)
        assert seen == [1.0], seen
        assert len(turns) == 1
        assert calls.process[0][1] is not None

        def _broken(fraction):
            raise ValueError("UI умер")

        turns = eng.diarize(pcm, progress=_broken)
        assert len(turns) == 1, "исключение в прогрессе не должно терять реплики"


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
