# TTS: облачные движки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (inline). Шаги отмечены `- [ ]`.

**Goal:** Добавить облачные TTS-движки Яндекс SpeechKit и Сбер SaluteSpeech как опции рядом с Silero/Piper.

**Architecture:** Новый общий базовый класс `_CloudTtsEngine` (HTTP→аудио→float32 PCM, дробление текста, ретраи); два подкласса (Yandex/Sber). Реализуют существующий контракт движка → pipeline не меняется, кроме пропуска `CloudTtsError` (сбой облака прерывает книгу, а не тишина). Секреты — в `settings.json`, движок читает их сам через `get_settings()`.

**Tech Stack:** Python 3.11, httpx, numpy, soundfile/av (в бандле), pydantic (settings), FastAPI, vanilla JS.

Спека: `docs/superpowers/specs/2026-07-25-tts-cloud-engines-design.md`.

---

## Файловая структура

**Создать:**
- `backend/tts/errors.py` — `CloudTtsError`.
- `backend/tts/engine_cloud_base.py` — `_CloudTtsEngine` + `_split_text`.
- `backend/tts/engine_yandex.py` — `YandexEngine` + каталог голосов.
- `backend/tts/engine_sber.py` — `SberEngine` + каталог голосов.

**Изменить:**
- `backend/schema.py` — `YandexSettings`, `SberSettings`; `TTSSettings.engine` Literal + поля `yandex`/`sber`.
- `backend/tts/factory.py` — `get_engine` для yandex/sber.
- `backend/tts/pipeline.py:128-134` — пропускать `CloudTtsError` (прерывать книгу).
- `backend/main.py` — `/api/tts/voices` (каталоги + `needs_config`); `/api/tts/synthesize` (engine yandex/sber, speaker/synth_opts); опц. `/api/tts/test`.
- `frontend/index.html` — опции yandex/sber в `#tts-engine`, блок `#tts-cloud-creds` (поля ключей).
- `frontend/static/tts.js` — `applyEngine` показывает creds-блок; сохранение ключей через `PUT /api/settings`.
- `build.ps1` + `build.sh` — `hiddenimports` для новых tts-модулей.
- `tests/test_tts.py` — тесты `_split_text` и lpcm-decode.

---

## Task 1: `CloudTtsError` + базовый класс + `_split_text` (с тестами)

**Files:** Create `backend/tts/errors.py`, `backend/tts/engine_cloud_base.py`; Modify `tests/test_tts.py`.

- [ ] Тест для `_split_text` (уважает лимит и границы предложений).
- [ ] Тест для lpcm-decode (int16 bytes → float32 [-1,1]).
- [ ] `errors.py`:
```python
class CloudTtsError(RuntimeError):
    """Персистентный сбой облачного TTS. pipeline прерывает книгу, а не подставляет тишину."""
```
- [ ] `engine_cloud_base.py`: `_split_text(text, limit)`; `_decode(content, fmt)` (lpcm через `np.frombuffer` int16 LE → float32/32768; WAV/OGG/MP3 через `soundfile`, фолбэк `av`); `_request(url,headers,body,fmt)` — POST через `httpx`, 3 ретрая с backoff на 429/5xx/сети, `CloudTtsError` на 401/403 и при истощении ретраев; `synth()` дробит текст, конкатенирует PCM; `get_status()` → `needs_config`/`ready`; `accepts_accent_marks=False`; `resolve_sample_rate` = `self.sample_rate`.
- [ ] `python tests/test_tts.py` — зелёный.

## Task 2: Схема настроек

**Files:** Modify `backend/schema.py`.

- [ ] Добавить `YandexSettings(api_key, folder_id, voice="alena")`, `SberSettings(oauth_token, voice="Nazar")`.
- [ ] `TTSSettings.engine: Literal["silero","piper","yandex","sber"]`; поля `yandex: YandexSettings`, `sber: SberSettings`.
- [ ] Проверка: `python -c "import json; from backend.schema import Settings; print(Settings().model_dump()['tts']['engine'])"`.

## Task 3: Яндекс-движок

**Files:** Create `backend/tts/engine_yandex.py`.

- [ ] `YANDEX_VOICES` (standard) + `YANDEX_HIFI` (nastya/maxim/dima/zlata); `voices`, `default_voice="alena"`, `sample_rate=48000`, `text_limit=4900`.
- [ ] `_credentials()` — читает `get_settings().tts.yandex`, поднимает `CloudTtsError` если нет api_key/folder_id.
- [ ] `_build_request(text, voice, creds)` → `POST tts.api.cloud.yandex.net/speech/v1/tts:synthesize`, заголовок `Authorization: Api-Key`, form-data `text/lang=ru-RU/voice/format=lpcm/sampleRateHertz=48000/folderId`, decode `lpcm`.
- [ ] `get_engine()`-синглтон + `get_status()` помечает hifi-голоса.
- [ ] Ручная проверка (с реальным ключом, не в CI): синтез короткой фразы → float32 PCM.

## Task 4: Сбер-движок

**Files:** Create `backend/tts/engine_sber.py`.

- [ ] `SBER_VOICES` (Nazar, Nikola, Kira, …); `default_voice="Nazar"`, `sample_rate=48000`, `text_limit=1000`.
- [ ] `_credentials()` — `get_settings().tts.sber.oauth_token`, иначе `CloudTtsError`.
- [ ] `_get_access(oauth)` — обмен OAuth → access-token (~30 мин, кэш+рефреш). **Точный эндпоинт/схема подтвердить по актуальным докам SaluteSpeech при реализации.**
- [ ] `_build_request` → `POST smartspeech.sber.ru/rest/v1/speech:synthesize`, `Authorization: Bearer <access>`, JSON `{audio_encoding:"WAV",voice,text}`, decode WAV через `soundfile`.
- [ ] `get_engine()`-синглтон.

## Task 5: Factory + pipeline (ошибки облака)

**Files:** Modify `backend/tts/factory.py`, `backend/tts/pipeline.py`.

- [ ] `factory.get_engine`: добавить `yandex`/`sber` → соответствующие синглтоны.
- [ ] `pipeline.py` synth-except: перед общей `except Exception` добавить `except CloudTtsError: raise` (импорт из `.errors`), чтобы сбой облака прерывал книгу, а не уходил в тишину.

## Task 6: Эндпоинты (`main.py`)

**Files:** Modify `backend/main.py`.

- [ ] `/api/tts/voices`: в `engines` добавить `yandex`/`sber` (`speakers`, `fixed_rate=True`, `needs_config`, `hifi?`).
- [ ] `/api/tts/synthesize`: расширить white-list engine (`silero/piper/yandex/sber`); для yandex/sber — `speaker = opts.get("speaker") or ts.<eng>.voice`, `synth_opts={}` (секреты движок берёт сам из settings); в `persist` сохранять выбранный голос.
- [ ] (опц.) `POST /api/tts/test` — синтез фразы «Тест» текущим движком для проверки ключа.

## Task 7: UI

**Files:** Modify `frontend/index.html`, `frontend/static/tts.js`.

- [ ] `index.html`: в `#tts-engine` добавить `<option value="yandex">Яндекс SpeechKit — облако</option>`, `<option value="sber">Сбер SaluteSpeech — облако</option>`. Добавить блок `#tts-cloud-creds` (поля: yandex api_key+folder_id, sber oauth_token; кнопка «Сохранить ключи»), скрыт по умолчанию.
- [ ] `tts.js`: `applyEngine` — показывать creds-блок и нужные поля при engine in {yandex,sber}; прятать частоту/ударения (fixed_rate). Сохранение ключей → `PUT /api/settings` c `{"tts":{"yandex":{...}}}`. На `loadVoices` — предзаполнить поля из `tts.yandex`/`tts.sber` и статус `needs_config`.
- [ ] Ручная проверка: выбрать Яндекс, ввести ключ, синтез фразы.

## Task 8: Сборка + финальная проверка

**Files:** Modify `build.ps1`, `build.sh`.

- [ ] Добавить `hiddenimports`: `backend.tts.errors`, `backend.tts.engine_cloud_base`, `backend.tts.engine_yandex`, `backend.tts.engine_sber`.
- [ ] `python tests/test_tts.py` — зелёный; импорт-чек `python -c "from backend.tts.factory import get_engine; get_engine('yandex'); get_engine('sber')"`.

## Self-review
- Spec coverage: errors/base/yandex/sber/factory/pipeline/main/ui/build/tests — все секции спеки покрыты (Task 1–8). ✅
- Sber auth endpoint — явная открытая точка (Task 4 помечено «подтвердить по докам»), совпадает со спекой §11.
- `CloudTtsError` имя едино во всех задачах. ✅
- `needs_config` статус едино. ✅
