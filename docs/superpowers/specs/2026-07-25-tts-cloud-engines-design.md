# TTS: облачные движки (Яндекс SpeechKit + Сбер SaluteSpeech)

- **Дата:** 2026-07-25
- **Статус:** одобрен, ожидает план реализации
- **Связано:** `backend/tts/factory.py`, `backend/tts/engine.py` (Silero), `backend/tts/engine_piper.py` (Piper)

## 1. Цель и проблема

Текущие локальные движки (Silero, Piper) звучат «роботски» для длинных русских
аудиокниг. Нужны голосы уровня «живой диктор», локально и бесплатно. На железе
пользователя (CPU, NVIDIA MX250 2 ГБ VRAM) качественные локальные модели
(CosyVoice 2 / XTTS v2) неприменимы: GPU слишком мал, а на CPU синтез книги
идёт часами. Лучший естественный русский по соотношению «качество / скорость /
ноль нагрузки на CPU» дают облачные TTS российских вендоров с бесплатными
квотами — Яндекс SpeechKit и Сбер SaluteSpeech.

**Решение:** добавить оба движка как новые опции рядом с Silero/Piper. Silero и
Piper остаются.

## 2. Объём

### В рамках спеки
- Два новых TTS-движка: Яндекс SpeechKit, Сбер SaluteSpeech.
- Общая база `_CloudTtsEngine` для HTTP-синтеза, дробления текста, декода аудио,
  ретраев.
- Расширение `factory.py`, схемы настроек, каталога голосов (`/api/tts/voices`),
  UI выбора движка/голоса и полей учётных данных.
- `hiddenimports` для новых модулей в `build.ps1` и `build.sh`.

### Вне рамок (явно)
- **CosyVoice 2** — см. «Future work»: блокер `ttsfrd` (только Linux x86_64 /
  Python 3.10, Windows-колеса нет).
- OS-keychain для секретов (пока plaintext в `settings.json`).
- Hi-Fi/премиум-эндпоинты Яндекса с особым контрактом (используем стандартный v1).

## 3. Архитектура

Оба движка реализуют **существующий контракт движка** (тот же, что Silero/Piper):

```
initialize() -> None
get_status() -> {status, engine, speakers?, error?}
list_speakers() -> list[str]
resolve_sample_rate(speaker, requested) -> int
synth(text, speaker, sample_rate, **opts) -> np.ndarray  # float32 mono [-1, 1]
accepts_accent_marks: bool
```

Следствие: вся цепочка синтеза (`reader → chunker → assemble → export → spool`)
**не меняется**. Движок — единственная точка расширения.

```
                    ┌─────────────────────────────┐
  pipeline ────────►│ get_engine(name) [factory]   │
                    └──────────────┬───────────────┘
            ┌──────────────┬───────┴───────┬───────────────┐
            ▼              ▼               ▼               ▼
        Silero         Piper         _CloudTtsEngine ◄── база
        (engine)    (engine_piper)      │       │
                                         ▼       ▼
                                    Yandex    Sber
                                 (engine_   (engine_
                                 yandex)    sber)
```

- **`engine_cloud_base.py`** — `_CloudTtsEngine`: HTTP через `httpx`,
  `_split_text(text, limit)` под лимит провайдера, `_decode_audio(bytes, fmt) ->
  float32 PCM`, ретраи с backoff, `accepts_accent_marks = False`.
- Подклассы реализуют только: построение запроса, авторизацию, каталог голосов,
  decode-специфику формата.

`factory.py`:

```python
def get_engine(name="silero", device="cpu"):
    n = (name or "").lower()
    if n == "piper":   ...engine_piper
    if n == "yandex":  ...engine_yandex
    if n == "sber":    ...engine_sber
    ...engine  # silero
```

## 4. Компоненты

### 4.1. Яндекс SpeechKit (`engine_yandex.py`)
- **API:** синхронный, `POST https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize`.
- **Тело:** `application/x-www-form-urlencoded`: `text`, `lang=ru-RU`, `voice`,
  `format=lpcm`, `sampleRateHertz=48000`, `folderId`, опц. `emotion`, `speed`.
- **Авторизация:** заголовок `Authorization: Api-Key <api_key>`.
- **Декод:** `lpcm` = сырой int16 LE mono → `np.frombuffer(..., int16).astype(float32) / 32768`.
  Запасной вариант при проблемах: `format=oggopus` + `av` (уже в бандле).
- **Дробление текста:** лимит Яндекса ~5000 символов на запрос; дробим
  безопасно по границам предложений (`razdel`/`.!?`) до ≤4900, конкатенируем PCM.
- **`resolve_sample_rate`:** 48000 (диктуется запросом; Hi-Fi голоса тоже 48k).
- **Каталог голосов** (статический список в модуле, отдаётся в `list_speakers` и
  `/api/tts/voices`): `alena`, `filipp`, `madirus`, `zborg` + Hi-Fi `nastya`,
  `maxim`, `dima`, `zlata`. Hi-Fi помечаются отдельно (поле `quality`).
- **Статус:** `needs_config`, если нет `api_key` или `folder_id`; иначе `ready`.

### 4.2. Сбер SaluteSpeech (`engine_sber.py`)
- **Авторизация:** OAuth-токен → обмен на short-lived access-token (~30 мин,
  кэшируется в памяти и рефрешится по истечении) → синтез.
- **Синтез:** `POST https://smartspeech.sber.ru/rest/v1/speech:synthesize` с
  JSON-телом (`voice`, `text`, `audio_encoding`).
- **Декод:** WAV/OGG/MP3 → float32 через `soundfile`/`av`.
- **Дробление текста:** по лимиту Сбера на запрос (уточнить точное значение при
  реализации; стартовое предположение ≤1000, дробим по предложениям).
- **Каталог голосов:** `Nazar`, `Nikola`, `Kira` (и др. по актуальному списку).
- **Статус:** `needs_config`, если нет `oauth_token`; иначе `ready`.
- **Замечание:** точные эндпоинты/схема payload Сбер могут меняться —
  подтвердить по актуальным докам SaluteSpeech при реализации. Спека фиксирует
  подход (OAuth → access-token → REST → декод), а не побайтовый контракт.

### 4.3. Настройки
Расширить `settings.json` + `backend/config.py`:

```jsonc
"tts": {
  "engine": "silero",            // silero | piper | yandex | sber
  ...
  "yandex": { "api_key": "", "folder_id": "", "voice": "alena" },
  "sber":   { "oauth_token": "", "voice": "Nazar" }
}
```

- `get_engine`/UI читают `tts.engine`; движок читает свою секцию.
- Валидация учётных данных → статус `needs_config`.

### 4.4. Эндпоинты (`backend/main.py`)
- `/api/tts/voices` — расширить: отдавать каталоги `yandex`/`sber` + флаг
  `needs_config` и `quality` (Hi-Fi) по каждому движку.
- `/api/tts/synthesize` — без изменений (движок берётся из `tts.engine`).
- **(новый, опциональный)** `POST /api/tts/test` — синтез короткой фразы текущим
  движком; для проверки ключа в UI настроек.

### 4.5. UI (`frontend/static/tts.js`, `index.html`, страница настроек)
- Dropdown движка: добавить `yandex`, `sber`.
- Dropdown голоса: наполняется per-engine из `/api/tts/voices`.
- Поля учётных данных для облака (API key + folder_id / OAuth-токен) — на
  странице настроек, с кнопкой «проверить» (`/api/tts/test`).
- Индикатор статуса `needs_config` → подсказка «введите ключ».

## 5. Поток данных (синтез книги)

1. UI → `POST /api/tts/synthesize` (текст + выбранный движок/голос).
2. `pipeline` дробит текст на фрагменты (как сейчас для Silero/Piper).
3. Каждый фрагмент → `engine.synth(...)`:
   - `_CloudTtsEngine.synth` дробит под лимит провайдера, шлёт HTTP-запрос(ы),
     декодит ответ в float32 PCM, конкатенирует.
4. PCM фрагментов склеивается `assemble`, нормализуется громкость (`spool`),
   экспортируется (`export`) в аудиокнигу.
5. Никаких изменений в шагах 2/4/5 — контракт движка сохранён.

## 6. Обработка ошибок

- **Транзиентные** (5xx, network, 429): ретрай ≤3 с экспоненциальным backoff.
- **401/403** (авторизация): движок переходит в `status=error` с сообщением
  «неверный ключ»; UI показывает ошибку.
- **Персистентный сбой** → бросает `CloudTtsError`.
  - **Важно:** в отличие от локальных движков, где `pipeline` подставляет тишину
    на непроизносимом фрагменте, **сбой облака не должен молча давать тишину** —
    иначе вся книга уйдёт в молчание. `CloudTtsError` прерывает генерацию и
    показывается в UI как ошибка уровня книги.
  - Точную точку перехвата (где `pipeline`/`export` отличает «фрагмент-тишина» от
    «критический сбой движка») уточнить при планировании по структуре
    `backend/tts/pipeline.py`, `reader.py`, `export.py`.

## 7. Сборка (PyInstaller)

- **Новых тяжёлых зависимостей нет:** `httpx`, `soundfile`, `av`, `razdel` уже
  бандлятся (для ASR/LLM/Piper).
- Добавить в `hiddenimports` (`build.ps1` + `build.sh`):
  `backend.tts.engine_cloud_base`, `backend.tts.engine_yandex`,
  `backend.tts.engine_sber`.
- Никаких скачиваний весов — облако не хранит модель локально.

## 8. Тестирование

- **Unit (с моком `httpx`):**
  - построение запроса и заголовков авторизации (Яндекс/Сбер);
  - декод `lpcm` int16 → float32 и WAV/OGG/MP3 → float32;
  - `_split_text` уважает лимит и границы предложений;
  - ретраи на 5xx/429, немедленный отказ на 401/403;
  - контракт: результат — `np.float32`, mono, значения в `[-1, 1]`.
- **Интеграционно (вручную, не в CI — нет ключей):** реальный синтез короткого
  текста обоими провайдерами после ввода ключей.
- **Статус `needs_config`:** UI корректно просит ключи при их отсутствии.

## 9. Безопасность

API-ключи и OAuth-токен хранятся **plaintext в `settings.json` рядом с exe**.
Обоснование: локальное однопользовательское десктоп-приложение, согласуется с
текущей моделью приложения (там уже лежат пользовательские настройки).
Рекомендация пользователю: не выкладывать `settings.json` в публичный доступ.
OS-keychain (Windows Credential Manager) — в Future work.

## 10. Future work

- **CosyVoice 2 (офлайн, голоса + клонирование).** Отложено из-за блокера:
  зависимость `ttsfrd` (текстовый фронтенд/G2P) распространяется только как
  `ttsfrd-*-cp310-cp310-linux_x86_64.whl` — нет Windows-колеса и нет сборки под
  Python 3.11 (проект на 3.11); также требует клон репозитория и
  `third_party/Matcha-TTS` на пути. Нативное встраивание в Windows PyInstaller-exe
  нерационально. Реалистичный путь на будущее — **sidecar в WSL2** (CosyVoice
  крутится в Linux-окружении, приложение общается с ним по HTTP на localhost).
  Вынести в отдельный проект (spec → план → реализация).
- OS-keychain для хранения секретов вместо plaintext `settings.json`.
- Hi-Fi/премиум-эндпоинты Яндекса с особым контрактом.

## 11. Открытые вопросы (уточнить при планировании/реализации)

1. Точные эндпоинты и payload SaluteSpeech (подтвердить по актуальным докам).
2. Лимит длины текста SaluteSpeech на запрос (стартовое допущение ≤1000).
3. Точка перехвата `CloudTtsError` в pipeline/export — где отличить
   «тишина на фрагменте» от «критический сбой движка».
4. Финальный список голосов и пометки Hi-Fi для Яндекса (по актуальному каталогу).
