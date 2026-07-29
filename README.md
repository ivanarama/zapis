# Записная книжка (Zapis)

Локальное десктопное приложение для работы с речью и текстом:

- **распознавание речи (ASR)** — аудио/видео в текст с таймкодами на уровне слов;
- **LLM-постобработка** — YouTube-описания, таймкоды, посты для Telegram, статьи, свободные вопросы к транскрипту (стриминг ответов в реальном времени);
- **озвучка текста (TTS)** — превращение `.txt` в аудиокнигу (`mp3`/`m4b`) одним из пяти движков.

Всё работает локально — модели скачиваются в кеш при первом использовании. Облако нужно только для облачных TTS (Яндекс/Сбер) и LLM. Расшифровки и озвучки сохраняются между сеансами.

## Возможности

**Распознавание речи (ASR)** — `backend/asr/`

- **GigaAM v3 CTC + KenLM (T-one)** — высокое качество для русского языка (по умолчанию).
- **faster-whisper** — мультиязычная модель (en, ru, es, de, fr, …), грузится лениво при первом запуске.
- Таймкоды на уровне слов с точностью до ~40 мс.

**LLM-постобработка** — `backend/llm/`

- 4 встроенных пресета (YouTube-описание, YouTube-таймкоды, Telegram-пост, Статья) + свободные вопросы к транскрипту.
- SSE-стриминг ответов по словам.
- Несколько профилей с fallback-цепочкой: порядок профилей = порядок попыток, порядок `models[]` внутри профиля = приоритет моделей.
- Провайдеры: `openai` (любой OpenAI-совместимый endpoint — Azure, OpenRouter, Qwen, DeepSeek, Ollama, LM Studio) и `anthropic`.
- Редактируемые промпты с разделением на `system` и `user_template`.

**Озвучка текста в аудиокнигу (TTS)** — `backend/tts/`

Пять движков через общий интерфейс (`initialize` / `get_status` / `list_speakers` / `synth`):

- **Silero** (по умолчанию) — офлайн на CPU, модель `v4_ru`, 5 голосов (aidar, baya, kseniya, xenia, eugene); понимает `+`-разметку ударений.
- **Piper** — офлайн, выше качество, голоса `rhasspy/piper-voices` (напр. `ru_RU-ruslan-medium`), регулятор темпа (`length_scale`).
- **edge-tts** (рекомендуемый «из коробки») — нейронные голоса Microsoft (`ru-RU-DmitryNeural` / `ru-RU-SvetlanaNeural`, 24 кГц), **без ключа и регистрации**.
- **Яндекс SpeechKit** — облако, нужны `api_key` + `folder_id`.
- **Сбер SaluteSpeech** — облако, нужны `client_id` / `client_secret` (обмениваются на access-token в рантайме).

Конвейер озвучки: загрузка `.txt` → разбиение на главы → нормализация чисел/дат/аббревиатур (rule-based через `num2words` **или** LLM с дисковым кэшем и защитой от «отсебятины») → расстановка ударений (`ruaccent`) → синтез → паузы между предложениями/абзацами/главами → сборка в **mp3 / m4b (с главами) / m4a / wav**. Прогресс стримится по SSE. Каждая озвучка сохраняется в историю.

> Сбой облачного движка бросает `CloudTtsError` и прерывает книгу — pipeline не подменяет тишину молча. `edge-tts` ходит на неофициальный endpoint и иногда транзиентно отдаёт «No audio» → ретрай с backoff.

**Экспорт и хранение**

- Расшифровки → TXT / SRT / VTT.
- Озвучки → mp3 / m4b / m4a / wav.
- Расшифровки и озвучки хранятся локально и доступны между сеансами (`backend/transcripts.py`, `backend/tts_runs.py`).

## Архитектура

```
Zapis/
├── main.py                      # Desktop entry point (pywebview)
├── backend/
│   ├── main.py                  # FastAPI: маршруты ASR / LLM / TTS / расшифровки / экспорт
│   ├── config.py                # settings.json: чтение, валидация, кеш
│   ├── schema.py                # Pydantic-модели (app / asr / llm / prompts / tts)
│   ├── formats.py               # SRT / VTT / TXT
│   ├── transcripts.py           # сохранённые расшифровки (persistence)
│   ├── tts_runs.py              # история озвучек (persistence)
│   ├── asr/
│   │   ├── base.py              # Transcriber Protocol
│   │   ├── factory.py           # фабрика и переключение движков
│   │   ├── gigaam_engine.py     # GigaAM v3 CTC + KenLM
│   │   └── whisper_engine.py    # faster-whisper (ленивая загрузка)
│   ├── llm/
│   │   ├── client.py            # AsyncOpenAI / AsyncAnthropic + fallback + SSE
│   │   └── prompts.py           # дефолты пресетов + сборка messages
│   └── tts/
│       ├── factory.py           # выбор движка по имени
│       ├── engine.py            # Silero (по умолчанию, офлайн)
│       ├── engine_piper.py      # Piper (офлайн, качество)
│       ├── engine_cloud_base.py # общий базис облачных движков
│       ├── engine_yandex.py     # Яндекс SpeechKit
│       ├── engine_sber.py       # Сбер SaluteSpeech
│       ├── engine_edge.py       # Microsoft edge-tts (без ключа)
│       ├── pipeline.py          # сборка аудиокниги (главы, паузы, экспорт)
│       ├── reader.py            # .txt → текст с разметкой глав
│       ├── chapters.py          # разбиение по главам
│       ├── chunker.py           # нарезка на фрагменты для синтеза
│       ├── normalize.py         # rule-based нормализация (num2words)
│       ├── normalize_cache.py   # кэш LLM-нормализации на диске
│       ├── stress.py            # ударения (ruaccent) перед синтезом
│       ├── assemble.py          # склейка PCM
│       ├── export.py            # mp3 / m4b / m4a / wav
│       ├── spool.py             # буферизация выходных файлов
│       └── errors.py            # CloudTtsError
├── frontend/
│   ├── index.html               # двухколоночный UI с табами
│   └── static/
│       ├── style.css            # тёмная/светлая тема через CSS-переменные
│       ├── app.js               # основной поток UI
│       ├── stream.js            # SSE через fetch + ReadableStream
│       ├── settings.js          # модалка настроек
│       ├── tts.js / tts.css     # вкладка «Озвучка»
├── settings.json                # пользовательские настройки
├── requirements.txt
├── build.ps1                    # сборка exe (Windows)
├── build.sh                     # сборка (Linux / macOS)
└── README.md
```

### Источник истины для GigaAM

`backend/asr/gigaam_engine.py` — обёртка над GigaAM v3 CTC и алгоритм Longform-склейки. Логика синхронизирована с отдельным сервисом `gigaam_tone` (`transcribe.py`), чьё ASR-ядро является source-of-truth.

**Процедура синхронизации при изменениях в `gigaam_tone`:**

1. Обновите `gigaam_tone/transcribe.py` (новая модель / правка алгоритма).
2. Перенесите изменения в классы `GigaAMCTC`, `LongformCTC`, `CTCDecoderWithLM`, `merge_ctc_log_probs_by_blank_sep`, `chunk_audio` и т.п. в `backend/asr/gigaam_engine.py`.
3. Прогоните smoke-тест транскрипции (раздел «Верификация»).

## Установка

```powershell
cd C:\Projects\Zapis
pip install -r requirements.txt
```

Дополнительно нужен **ffmpeg** в `PATH` — он используется для декодирования произвольных аудио/видео в 16 кГц mono.

### GigaAM v3 — установка с GitHub

PyPI-версия пакета `gigaam` поддерживает только до v2. Для v3 пакет ставится прямо из репозитория Salute Developers — эта строка уже включена в `requirements.txt` (пин на коммит):

```powershell
pip install --force-reinstall git+https://github.com/salute-developers/GigaAM.git
```

Если после `pip install -r requirements.txt` приложение пишет `Model 'v3_ctc' not found` — значит осталась старая PyPI-версия, выполните команду выше вручную с `--force-reinstall`.

## Настройка

`settings.json` (поля можно править из UI: **Настройки → ASR / LLM-профили / Промпты / Озвучка / Вид**):

```json
{
  "app":  { "title": "Записная книжка", "port": 8001, "theme": "dark" },
  "asr":  {
    "engine": "gigaam",
    "language": "ru",
    "gigaam":  { "version": "v3" },
    "whisper": { "model": "small" }
  },
  "llm": {
    "temperature": 0.3,
    "max_tokens": 4096,
    "profiles": [
      {
        "name": "openai",
        "api_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-…",
        "models": ["gpt-4o", "gpt-4o-mini"]
      }
    ]
  },
  "prompts": {
    "youtube_description": { "system": "", "user_template": "" },
    "youtube_timecodes":   { "system": "", "user_template": "" },
    "telegram_post":       { "system": "", "user_template": "" },
    "article":             { "system": "", "user_template": "" },
    "custom_system": "",
    "tts_normalize":       { "system": "", "user_template": "" }
  },
  "tts": {
    "engine": "silero",
    "language": "ru",
    "silero": { "speaker": "baya", "sample_rate": 48000 },
    "piper":  { "speaker": "ru_RU-ruslan-medium", "length_scale": 1.0 },
    "edge":   { "voice": "ru-RU-DmitryNeural" },
    "yandex": { "api_key": "", "folder_id": "", "voice": "alena" },
    "sber":   { "client_id": "", "client_secret": "", "voice": "Nazar" },
    "pauses": { "sentence": 300, "paragraph": 700, "chapter": 1500 },
    "normalize": { "use_llm": false },
    "accent":    { "enabled": true, "model_size": "tiny" },
    "export":    { "format": "mp3", "split_chapters": true, "bitrate": 128000 }
  }
}
```

Пустые поля в `prompts.*` означают «использовать встроенный шаблон». Секреты облачных TTS и LLM хранятся plaintext — приложение локальное и однопользовательское.

### LLM: профили и fallback

- Порядок профилей в массиве = порядок попыток. Если первый профиль возвращает ошибку до первого чанка ответа, движок переходит к следующему.
- Внутри профиля порядок `models[]` — тоже приоритет (для одного URL пробуются разные модели по очереди).

## Запуск

```powershell
python main.py
```

Откроется окно pywebview. Модель GigaAM подгружается лениво при первой транскрибации (при первом запуске может занять несколько минут — скачивается с HuggingFace). Whisper и TTS-модели (Silero, ruaccent, голоса Piper) тоже грузятся при первом использовании соответствующей функции.

## Использование

**Расшифровка:**

1. Выберите движок (GigaAM для русского, Whisper — для прочих языков).
2. Перетащите файл в зону загрузки.
3. Нажмите «Транскрибировать», дождитесь результата (вкладка «Транскрипт»).
4. Экспортируйте в TXT/SRT/VTT.

**ИИ-обработка:** перейдите на вкладку «ИИ-обработка», нажмите пресет или задайте свой вопрос — ответ стримится по словам.

**Озвучка:**

1. Перейдите на вкладку «Озвучка», загрузите `.txt` (книгу/статью/расшифровку).
2. Выберите движок и голос (для пробы без настройки — `edge-tts`).
3. При необходимости включите LLM-нормализацию чисел/аббревиатур и расстановку ударений.
4. Нажмите «Озвучить» — прогресс стримится по главам; результат сохранится в `audiobooks/` и в истории озвучек.

## Сборка

| Платформа        | Команда        | Результат          |
|------------------|----------------|--------------------|
| Windows          | `.\build.ps1`  | `dist\Zapis.exe`   |
| Linux            | `bash build.sh`| `dist/Zapis`       |
| macOS (Apple Silicon) | `bash build.sh` | `dist/Zapis.app` |

Скрипт ставит зависимости в локальный venv, ставит `pyctcdecode` без конфликтующих deps и собирает PyInstaller-бинарь. Дефолтный `settings.json` кладётся рядом только при первом билде — пользовательский не затирается.

> **Модели (GigaAM, KenLM, Whisper, Silero, ruaccent, голоса Piper) НЕ пакуются** в бинарь — они скачиваются в кеш при первом использовании. Это держит размер дистрибутива в разумных пределах.
>
> **KenLM** имеет готовые wheels для Linux/macOS. На Windows это C++-расширение: компилируется через MSVC (CI ставит `microsoft/setup-msbuild`) либо, без MSVC, движок откатывается на greedy-декодер.

### CI/CD

`.github/workflows/build.yml` собирает все платформы при пуше в `main` и при пуше тега `v*`:

- **Windows / Linux / macOS Apple Silicon** — основные job'ы, определяют зелёный статус.
- **macOS Intel (`macos-13`)** — `continue-on-error`: собирается «по возможности», не блокирует статус и релиз (Intel Mac снят с продаж в 2020, сборка медленная и нестабильная).
- При пуше тега `v*` артефакты автоматически аттачатся к **GitHub Release** (через `softprops/action-gh-release`). macOS-бандл `.app` запаковывается в zip (релиз принимает только файлы).

## Верификация

1. **GigaAM v3** — взять русскоязычное `.mp3` (1–3 мин), запустить транскрипцию, убедиться что текст корректный, экспортировать SRT, открыть в плеере.
2. **faster-whisper** — переключить движок, выбрать язык `en`, загрузить английский `.mp4`. Первый запуск качает модель (`small` ≈ 500 MB).
3. **LLM-стриминг** — настроить рабочий профиль, нажать «YouTube таймкоды»: текст должен появляться по чанкам.
4. **Fallback LLM** — поставить первым профиль с заведомо нерабочим ключом, вторым — рабочий. Запрос должен пройти со второго.
5. **Custom-чат** — задать «Сделай 5 ключевых тезисов» — ответ стримится.
6. **TTS (edge-tts)** — на вкладке «Озвучка» выбрать `edge-tts`, загрузить `.txt`, «Озвучить» → проверить `mp3`/`m4b` в `audiobooks/`.
7. **TTS-нормализация** — включить LLM-нормализацию на тексте с числами/датами, перегенерировать — числа должны произноситься словами.

## Публикации

[**Этот репозиторий использован в статье на Инфостарт**](https://infostart.ru/1c/articles/2748070/)

<img src="https://infostart.ru/bitrix/templates/sandbox_empty/assets/tpl/abo/img/logo.svg" alt="Инфостарт" width="120">

---
