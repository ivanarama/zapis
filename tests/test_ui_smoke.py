"""UI-smoke тесты фронтенда через playwright (issue #10).

Сервер — настоящий FastAPI (uvicorn в потоке, lifespan выключен: движки на
старте не создаются). Все запросы /api/* перехватываются на стороне браузера
(page.route) и отвечают заглушкой FakeAPI: тесты герметичны — settings.json,
модели и хранилище расшифровок не трогаются, а поведение API фиксируется
контрактом (бэкенд-слой покрыт TestClient-тестами).

Проверяются регрессии, которые уже болели: скрытие галочки диаризации без
пакета, состав PUT /api/settings при смене устройства (перезапуск не нужен),
блокировка кнопки на время транскрипции, честный alert при 409, рендер
ошибки движка в статусе.

Установка:  pip install playwright  &&  playwright install chromium
Запуск:     python tests\\test_ui_smoke.py   (или через pytest, если установлен)
"""

import json
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 8

_ERROR_TEXT = (
    "Не удалось запустить Whisper на видеокарте. Нужны NVIDIA-драйвер с "
    'поддержкой CUDA 12 и библиотека cuDNN 9. Поставьте asr.whisper.device = "cpu".'
)


# ---------- сервер (один на весь прогон) ----------

_server_lock = threading.Lock()
_server = None  # (uvicorn.Server, port)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_server():
    """Настоящее приложение, но lifespan off: UI-тестам не нужны движки,
    создаваемые на старте (импорт torch — секунды на каждый прогон)."""
    global _server
    with _server_lock:
        if _server is None:
            from backend.main import app
            import uvicorn

            port = _free_port()
            config = uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="warning", lifespan="off",
            )
            srv = uvicorn.Server(config)
            threading.Thread(target=srv.run, daemon=True).start()
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                    _server = (srv, port)
                    break
                except Exception:
                    time.sleep(0.1)
            if _server is None:
                raise RuntimeError("тестовый сервер не поднялся за 30 с")
    return _server


# ---------- заглушка API ----------


def _default_settings():
    """Зеркало дефолтов Settings из backend/schema.py — settings.js читает
    все блоки насквозь, и отсутствие поля не должно влиять на исход теста."""
    return {
        "app": {"title": "Записная книжка", "port": 8001, "theme": "dark"},
        "asr": {
            "engine": "gigaam",
            "language": "ru",
            "device": "auto",
            "gigaam": {"version": "v3", "device": None},
            "whisper": {"model": "small", "cpu_threads": 0, "device": None},
            "diarization": {
                "enabled": False,
                "num_speakers": 0,
                "threshold": 0.5,
                "embedding_model": "wespeaker_en_voxceleb_resnet34_LM.onnx",
                "num_threads": 0,
                "window_shift_ratio": 0.3,
            },
        },
        "llm": {
            "api_key": "", "base_url": "", "api_provider": "openai",
            "profiles": [], "temperature": 0.3, "max_tokens": 4096,
        },
        "prompts": {
            "youtube_description": {"system": "", "user_template": ""},
            "youtube_timecodes": {"system": "", "user_template": ""},
            "telegram_post": {"system": "", "user_template": ""},
            "article": {"system": "", "user_template": ""},
            "custom_system": "",
            "tts_normalize": {"system": "", "user_template": ""},
        },
        "tts": {
            "engine": "silero", "language": "ru", "device": "cpu",
            "silero": {"version": "v4_ru", "speaker": "baya", "sample_rate": 48000,
                       "put_accent": True, "put_yo": True},
            "piper": {"speaker": "ru_RU-ruslan-medium", "length_scale": 1.0},
            "yandex": {"api_key": "", "folder_id": "", "voice": "alena",
                       "emotion": "neutral"},
            "sber": {"client_id": "", "client_secret": "", "voice": "Nazar"},
            "edge": {"voice": "ru-RU-DmitryNeural"},
            "pauses": {"sentence": 300, "paragraph": 700, "chapter": 1500},
            "pause_each_sentence": False,
            "normalize": {"use_llm": False},
            "accent": {"enabled": True, "model_size": "tiny"},
            "export": {"format": "mp3", "split_chapters": True, "bitrate": 128000},
            "chapter_pattern": "",
        },
    }


class FakeAPI:
    """Все /api/* отвечают заглушками; внешние хосты обрываются, статику
    отдаёт настоящий сервер. Запросы логируются — состав payload'ов и есть
    предмет проверки."""

    def __init__(self):
        self.settings = _default_settings()
        self.status = {"status": "idle", "engine": "gigaam", "detail": "GigaAM v3"}
        self.diarization = {
            "available": False,
            "enabled": False,
            "num_speakers": 0,
            "threshold": 0.5,
            "embedding_model": "wespeaker_en_voxceleb_resnet34_LM.onnx",
            "window_shift_ratio": 0.3,
            "models": {},
            "models_ready": False,
            "status": {"status": "idle", "engine": "diarization"},
            "install_hint": "Пакет sherpa-onnx не установлен, диаризация недоступна.",
        }
        self.transcribe_status = 200
        self.transcribe_body = {
            "ok": True,
            "result": {"text": "привет", "language": "ru", "segments": [
                {"start": 0.0, "end": 1.0, "text": "привет"},
            ]},
        }
        self.hold_transcribe = False
        self.settings_payloads = []
        self.transcribe_urls = []
        self.held = []

    def install(self, page):
        page.route("**/*", self._handle)

    def _fulfill_json(self, route, payload, status=200):
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def _handle(self, route):
        req = route.request
        url = urlparse(req.url)
        if url.hostname not in ("127.0.0.1", "localhost"):
            route.abort()  # web-шрифт и прочая внешняя — тесты ждём без сети
            return
        path = url.path
        if not path.startswith("/api/"):
            route.continue_()  # / и /static отдаёт настоящий сервер
        elif path == "/api/settings" and req.method == "PUT":
            self.settings_payloads.append(json.loads(req.post_data))
            self._fulfill_json(route, {"ok": True, "settings": self.settings})
        elif path == "/api/settings":
            self._fulfill_json(route, self.settings)
        elif path == "/api/asr/status":
            self._fulfill_json(route, self.status)
        elif path == "/api/asr/diarization":
            self._fulfill_json(route, self.diarization)
        elif path == "/api/asr/engines":
            self._fulfill_json(route, {
                "engines": [
                    {"name": "gigaam", "languages": ["ru"], "active": True},
                    {"name": "whisper", "languages": ["auto", "en", "ru"]},
                ],
                "active": "gigaam", "language": "ru",
            })
        elif path == "/api/transcribe" and req.method == "POST":
            self.transcribe_urls.append(req.url)
            if self.hold_transcribe:
                # Не отвечаем: запрос висит, UI остаётся в состоянии busy.
                # fulfill вызовем позже из теста — стандартный паттерн gate.
                self.held.append(route)
                return
            self._fulfill_json(
                route, self.transcribe_body, status=self.transcribe_status,
            )
        elif path == "/api/transcripts" and req.method == "POST":
            self._fulfill_json(route, {"ok": True, "transcript": {"id": "t1"}})
        elif path == "/api/transcripts":
            self._fulfill_json(route, {"transcripts": []})
        elif path == "/api/prompts":
            self._fulfill_json(route, {
                "current": self.settings["prompts"],
                "defaults": self.settings["prompts"],
            })
        elif path == "/api/tts/voices":
            self._fulfill_json(route, {
                "engine": "silero",
                "engines": {
                    "silero": {"speakers": ["baya", "xenia"], "fixed_rate": False},
                    "piper": {"speakers": ["ru_RU-ruslan-medium"], "fixed_rate": True},
                    "yandex": {"speakers": [], "fixed_rate": True, "cloud": True,
                               "needs_config": True, "hifi": []},
                    "sber": {"speakers": [], "fixed_rate": True, "cloud": True,
                             "needs_config": True, "hifi": []},
                    "edge": {"speakers": [], "fixed_rate": True, "cloud": True,
                             "needs_config": False, "hifi": []},
                },
                "speakers": ["baya", "xenia"],
                "tts": self.settings["tts"],
            })
        elif path == "/api/tts/runs":
            self._fulfill_json(route, {"runs": []})

    def release_transcribe(self, payload=None, status=None):
        """Отвечает на удержанный POST /api/transcribe."""
        routes, self.held = self.held, []
        for r in routes:
            self._fulfill_json(
                r,
                self.transcribe_body if payload is None else payload,
                status=self.transcribe_status if status is None else status,
            )


# ---------- прогон страницы ----------

_pw = None
_browser = None


def _new_page(fake):
    global _pw, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright

        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=True)
    _, port = _get_server()
    context = _browser.new_context()
    page = context.new_page()
    fake.install(page)
    page.goto(f"http://127.0.0.1:{port}/")
    return context, page


def _upload_wav(page):
    page.set_input_files("#file-input", files=[{
        "name": "record.wav", "mimeType": "audio/wav", "buffer": WAV_BYTES,
    }])


def _close(context):
    if context:
        context.close()


# ---------- тесты ----------


def test_page_loads_and_idle_status_disables_transcribe():
    """Страница живая: статус idle читается как готовность к первому запуску,
    кнопка транскрипции без файла не активна."""
    fake = FakeAPI()
    context, page = _new_page(fake)
    try:
        page.wait_for_function(
            "document.querySelector('#asr-status').textContent"
            ".includes('загрузится при первом запуске')"
        )
        assert page.eval_on_selector(
            "#asr-status", "el => el.classList.contains('status--ready')"
        )
        assert page.is_disabled("#btn-transcribe"), "без файла кнопка обязана быть выключена"
    finally:
        _close(context)


def test_diarization_hidden_when_package_unavailable():
    """sherpa-onnx нет: галочка на главной скрыта целиком; в настройках —
    подсказка об установке и задизейбленная кнопка скачивания."""
    fake = FakeAPI()
    context, page = _new_page(fake)
    try:
        page.wait_for_function(
            "document.querySelector('#diarization-box').hidden === true"
        )
        page.click("#btn-settings")
        page.wait_for_selector("#settings-modal", state="visible")
        page.wait_for_function(
            "document.querySelector('#settings-diarization-enabled') !== null"
            " && document.querySelector('#settings-device').value !== ''"
        )
        hint = page.text_content("#diarization-models-state")
        assert "sherpa-onnx" in hint, hint
        assert page.is_disabled("#btn-download-diarization")
    finally:
        _close(context)


def test_diarization_enabled_sends_params_and_download_note():
    """Диаризация доступна и включена: галочка отмечена, поле числа говорящих
    видно, без скачанных моделей — подсказка про 34 МБ, а POST /api/transcribe
    уходит с diarize=true и speakers."""
    fake = FakeAPI()
    fake.diarization.update({
        "available": True,
        "enabled": True,
        "num_speakers": 2,
        "models": {"wespeaker_en_voxceleb_resnet34_LM.onnx": "WeSpeaker"},
        "models_ready": False,
    })
    context, page = _new_page(fake)
    try:
        page.wait_for_function(
            "document.querySelector('#diarization-box').hidden === false"
        )
        assert page.is_checked("#chk-diarize")
        assert page.eval_on_selector(
            "#speakers-field", "el => el.hidden === false"
        )
        page.wait_for_function(
            "document.querySelector('#diarization-note').textContent"
            ".includes('34 МБ')"
        )
        page.wait_for_function(
            "document.querySelector('#num-speakers').value === '2'"
        )

        _upload_wav(page)
        page.wait_for_function("!document.querySelector('#btn-transcribe').disabled")
        page.click("#btn-transcribe")
        page.wait_for_function(
            "document.querySelector('#transcript').textContent.includes('привет')"
        )
        assert fake.transcribe_urls, "POST /api/transcribe не ушёл"
        params = urlparse(fake.transcribe_urls[0]).query
        assert "diarize=true" in params, params
        assert "speakers=2" in params, params
    finally:
        _close(context)


def test_settings_save_carries_device_change_without_restart():
    """Смена устройства в настройках: PUT /api/settings уходит с asr.device=cuda,
    пустые переопределения движков сериализуются в null (контракт схемы), окно
    закрывается без ошибок — перезапуск для применения не нужен."""
    fake = FakeAPI()
    context, page = _new_page(fake)
    try:
        page.click("#btn-settings")
        page.wait_for_selector("#settings-modal", state="visible")
        page.wait_for_function(
            "document.querySelector('#settings-device').value === 'auto'"
        )
        page.select_option("#settings-device", "cuda")
        page.click("#btn-save-settings")
        page.wait_for_function(
            "document.querySelector('#settings-modal').hidden === true"
        )
        assert fake.settings_payloads, "PUT /api/settings не ушёл"
        body = fake.settings_payloads[-1]
        assert body["asr"]["device"] == "cuda", body["asr"]
        assert body["asr"]["gigaam"]["device"] is None, (
            "пустое «Как общее» обязано сохраниться как null, а не ''"
        )
        assert body["asr"]["whisper"]["device"] is None
    finally:
        _close(context)


def test_engine_error_status_shown_and_blocks_transcribe():
    """Ошибка движка (Whisper-cuda без cuDNN) видна в статусе целиком, а кнопка
    транскрипции не активируется даже с выбранным файлом."""
    fake = FakeAPI()
    fake.status = {"status": "error", "engine": "whisper", "error": _ERROR_TEXT}
    context, page = _new_page(fake)
    try:
        page.wait_for_selector("#asr-status.status--error")
        text = page.text_content("#asr-status")
        assert "Ошибка:" in text and "cuDNN" in text, text
        _upload_wav(page)
        time.sleep(0.3)  # acceptFile пересчитывает кнопку синхронно
        assert page.is_disabled("#btn-transcribe"), (
            "при ошибке движка кнопка обязана оставаться выключенной"
        )
    finally:
        _close(context)


def test_busy_transcription_disables_button_until_done():
    """Пока транскрипция идёт (запрос удержан): прогресс виден, кнопка выключена;
    после ответа — транскрипт отрисован, экспорт разблокирован, кнопка снова
    активна."""
    fake = FakeAPI()
    fake.status = {"status": "ready", "engine": "gigaam", "detail": "GigaAM v3"}
    fake.hold_transcribe = True
    context, page = _new_page(fake)
    try:
        page.wait_for_function(
            "document.querySelector('#asr-status').textContent.includes('Готово')"
        )
        _upload_wav(page)
        page.wait_for_function("!document.querySelector('#btn-transcribe').disabled")
        page.click("#btn-transcribe")
        page.wait_for_function("!document.querySelector('#progress').hidden")
        assert page.is_disabled("#btn-transcribe"), (
            "посреди транскрипции кнопка обязана быть выключена (иначе второй "
            "клик получает 409)"
        )
        fake.release_transcribe()
        page.wait_for_function(
            "document.querySelector('#transcript').textContent.includes('привет')"
        )
        page.wait_for_selector("#export-card", state="visible")
        page.wait_for_function(
            "document.querySelector('#progress').hidden"
            " && !document.querySelector('#btn-transcribe').disabled"
        )
    finally:
        _close(context)


def test_409_response_alerts_with_server_text():
    """Если 409 всё же пришёл (второе окно, API-клиент): пользователю
    показывается текст сервера, а не тишина; после alert состояние busy
    сбрасывается."""
    fake = FakeAPI()
    fake.status = {"status": "ready", "engine": "gigaam", "detail": "GigaAM v3"}
    fake.transcribe_status = 409
    fake.transcribe_body = {
        "error": "Транскрипция уже идёт — дождитесь её завершения",
    }
    context, page = _new_page(fake)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    try:
        page.wait_for_function(
            "document.querySelector('#asr-status').textContent.includes('Готово')"
        )
        _upload_wav(page)
        page.wait_for_function("!document.querySelector('#btn-transcribe').disabled")
        page.click("#btn-transcribe")
        page.wait_for_function(
            "document.querySelector('#progress').hidden"
            " && !document.querySelector('#btn-transcribe').disabled"
        )
        assert dialogs == [
            "Ошибка: Транскрипция уже идёт — дождитесь её завершения"
        ], dialogs
    finally:
        _close(context)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    try:
        for t in tests:
            try:
                t()
                print(f"  ok  {t.__name__}")
            except Exception as e:  # noqa: BLE001 — один упавший тест не роняет прогон
                failed += 1
                print(f"FAIL  {t.__name__}: {e!r}")
    finally:
        if _browser is not None:
            _browser.close()
            _pw.stop()
        if _server is not None:
            _server[0].should_exit = True
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
