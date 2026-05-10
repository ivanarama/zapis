# Записная книжка

Локальное десктопное приложение для транскрибации аудио/видео в текст с таймкодами и постобработкой через LLM (YouTube-описания, таймкоды, посты для Telegram, статьи, свободные вопросы к транскрипту).

## Возможности

- **Локальное распознавание речи** двумя движками на выбор:
  - **GigaAM v3 CTC + KenLM (T-one)** — высокое качество для русского языка (по умолчанию).
  - **faster-whisper** — мультиязычная модель (en, ru, es, de, fr, …), модель грузится лениво при первом запуске.
- **Таймкоды на уровне слов** с точностью до 40 мс.
- **Экспорт** результата в TXT / SRT / VTT.
- **LLM-постобработка** с поддержкой нескольких профилей и fallback-цепочки:
  - 4 встроенных пресета (YouTube описание, YouTube таймкоды, Telegram пост, Статья).
  - Свободные вопросы к транскрипту (custom-сценарий).
  - **SSE-стриминг** ответов LLM в реальном времени.
  - Редактируемые промпты в настройках.

## Архитектура

```
Zapis/
├── main.py                  # Desktop entry point (pywebview)
├── backend/
│   ├── main.py              # FastAPI: маршруты + DI ASR/LLM
│   ├── config.py            # settings.json: чтение, валидация, кеш
│   ├── schema.py            # Pydantic-модели настроек
│   ├── formats.py           # SRT/VTT/TXT, общая сборка слов→сегментов
│   ├── asr/
│   │   ├── base.py          # Transcriber Protocol
│   │   ├── factory.py       # фабрика и переключение движков
│   │   ├── gigaam_engine.py # GigaAM v3 (синхронизирован с gigaam_tone)
│   │   └── whisper_engine.py# faster-whisper (ленивая загрузка)
│   └── llm/
│       ├── client.py        # AsyncOpenAI / AsyncAnthropic + fallback + SSE
│       └── prompts.py       # дефолты пресетов + сборка messages
├── frontend/
│   ├── index.html           # двухколоночный UI с табами
│   └── static/
│       ├── style.css        # тёмная/светлая тема через CSS-переменные
│       ├── app.js           # основной поток UI
│       ├── stream.js        # SSE через fetch+ReadableStream
│       └── settings.js      # модалка настроек (4 вкладки)
├── settings.json            # пользовательские настройки
├── requirements.txt
├── build.ps1                # сборка exe через PyInstaller
└── README.md
```

### Источник истины для GigaAM

Файл `backend/asr/gigaam_engine.py` содержит обёртку над GigaAM v3 CTC и алгоритм Longform-склейки. Логика синхронизирована с `C:/Projects/gigaam_tone/transcribe.py` — это отдельный сервис, его ASR-ядро и есть source-of-truth.

**Процедура синхронизации при изменениях в gigaam_tone:**

1. Обновите `gigaam_tone/transcribe.py` (новая модель / правка алгоритма).
2. Перенесите изменения в классах `GigaAMCTC`, `LongformCTC`, `CTCDecoderWithLM`, `merge_ctc_log_probs_by_blank_sep`, `chunk_audio` и т.п. в `backend/asr/gigaam_engine.py`.
3. Прогоните smoke-тест транскрипции (раздел «Верификация» ниже).

## Установка

```powershell
cd C:\Projects\Zapis
pip install -r requirements.txt
```

Дополнительно нужен **ffmpeg** в `PATH` — он используется для декодирования произвольных аудио/видео в 16 кГц mono.

### GigaAM v3 — установка с GitHub

PyPI-версия пакета `gigaam` поддерживает только до v2. Для v3 пакет ставится прямо из репозитория Salute Developers (как в `C:/Projects/gigaam_tone/Dockerfile`):

```powershell
pip install --force-reinstall git+https://github.com/salute-developers/GigaAM.git
```

Эта строка уже включена в `requirements.txt`. Если после `pip install -r requirements.txt` приложение пишет ошибку «`Model 'v3_ctc' not found`» — значит у вас осталась старая PyPI-версия, выполните команду выше вручную с `--force-reinstall`.

## Настройка

`settings.json`:

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
    "custom_system": ""
  }
}
```

Все блоки можно править из UI: **Настройки → ASR / LLM-профили / Промпты / Вид**. Пустые поля в `prompts.*` означают «использовать встроенный шаблон».

### LLM: профили и fallback

- Порядок профилей в массиве = порядок попыток. Если первый профиль возвращает ошибку до первого чанка ответа, движок переходит к следующему.
- Внутри профиля порядок `models[]` — тоже приоритет (для одного URL-а пробуются разные модели по очереди).
- Поддерживаются провайдеры: `openai` (любой OpenAI-совместимый endpoint, включая Azure/OpenRouter/Qwen/DeepSeek/Ollama/LM Studio) и `anthropic`.

## Запуск

```powershell
python main.py
```

Откроется окно pywebview. Модель GigaAM подгружается на старте (при первом запуске может занять несколько минут — скачивается с HuggingFace). Whisper-модель грузится только при первой транскрипции на этом движке.

## Использование

1. Выберите движок (GigaAM по умолчанию для русского, Whisper — для прочих языков).
2. Перетащите файл в зону загрузки.
3. Нажмите «Транскрибировать», дождитесь результата (вкладка «Транскрипт»).
4. Экспортируйте в TXT/SRT/VTT (левая панель).
5. Перейдите на вкладку «ИИ-обработка», нажмите пресет или задайте свой вопрос — ответ стримится по словам.

## Сборка exe

```powershell
.\build.ps1
```

Готовый файл: `dist\Zapis.exe` + `dist\settings.json`.

> Модели (GigaAM, KenLM, Whisper) **не пакуются** в exe — они скачиваются в кеш HuggingFace при первом использовании. Это сохраняет размер дистрибутива в разумных пределах.

## Верификация

1. **GigaAM v3** — взять русскоязычное `.mp3` (1–3 мин), запустить транскрипцию, убедиться что текст корректный, экспортировать SRT, открыть в плеере.
2. **faster-whisper** — переключить движок в селекторе, выбрать язык `en`, загрузить английский `.mp4`. Первый запуск качает модель (`small` ≈ 500 MB).
3. **LLM-стриминг** — настроить рабочий профиль, нажать «YouTube таймкоды»: текст должен появляться по чанкам.
4. **Fallback LLM** — поставить первым профиль с заведомо нерабочим ключом, вторым — рабочий. Запрос должен пройти со второго.
5. **Custom-чат** — задать «Сделай 5 ключевых тезисов» — ответ стримится.
6. **Промпты** — изменить шаблон «Telegram пост» в настройках, перегенерировать — текст должен отражать правки.
