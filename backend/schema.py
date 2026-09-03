"""Pydantic-схема settings.json.

LLM-блок построен по образцу localChat: список профилей с порядком = приоритет
fallback. Внутри профиля список моделей — тоже с приоритетом."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class LLMProfile(BaseModel):
    name: str = ""
    api_provider: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    api_key: str = ""
    models: list[str] = Field(default_factory=list)


class LLMSettings(BaseModel):
    # общие дефолты — используются, если в профиле поле пустое
    api_key: str = ""
    base_url: str = ""
    api_provider: Literal["openai", "anthropic"] = "openai"
    profiles: list[LLMProfile] = Field(default_factory=list)
    temperature: float = 0.3
    max_tokens: int = 4096

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        def_url = (data.get("base_url") or "").strip()
        def_key = data.get("api_key") or ""
        def_prov = data.get("api_provider") or "openai"

        legacy_model = data.get("model")
        legacy_models = data.get("models")
        legacy_list: list[str] = []
        if isinstance(legacy_models, list):
            legacy_list = [str(m).strip() for m in legacy_models if str(m).strip()]
        if not legacy_list and legacy_model:
            legacy_list = [str(legacy_model).strip()]

        profiles_in = data.get("profiles")
        norm: list[dict[str, Any]] = []
        if isinstance(profiles_in, list):
            for idx, p in enumerate(profiles_in):
                if not isinstance(p, dict):
                    continue
                purl = (p.get("base_url") or "").strip() or def_url
                pkey = p.get("api_key") or ""
                pmods = p.get("models")
                pm: list[str] = []
                if isinstance(pmods, list):
                    pm = [str(x).strip() for x in pmods if str(x).strip()]
                if not pm:
                    continue
                norm.append({
                    "name": p.get("name") or f"profile-{idx + 1}",
                    "api_provider": p.get("api_provider") or def_prov,
                    "base_url": purl,
                    "api_key": pkey,
                    "models": pm,
                })

        if not norm and (legacy_list or def_key or def_url):
            norm.append({
                "name": "default",
                "api_provider": def_prov,
                "base_url": def_url,
                "api_key": str(def_key),
                "models": legacy_list,
            })

        data["profiles"] = norm
        return data


class GigaamSettings(BaseModel):
    version: Literal["v2", "v3"] = "v3"
    # None = взять asr.device. GigaAM считает на torch, а в дистрибутиве torch
    # собран без CUDA, поэтому "cuda" здесь сработает только в сборке с
    # CUDA-колесом torch — иначе движок сам откатится на CPU.
    device: Optional[Literal["auto", "cpu", "cuda"]] = None


class WhisperSettings(BaseModel):
    model: Literal["tiny", "base", "small", "medium", "large-v2", "large-v3"] = "small"
    # Потоков CPU для CTranslate2. 0 = по числу ядер (иначе движок молча
    # берёт свои 4 и не разгоняется на многоядерных машинах).
    cpu_threads: int = Field(default=0, ge=0, le=256)
    # None = взять asr.device. Отдельная настройка нужна потому, что Whisper
    # считает на CTranslate2, а не на torch: GPU может быть доступен ему даже
    # там, где GigaAM вынужден остаться на CPU.
    device: Optional[Literal["auto", "cpu", "cuda"]] = None


class DiarizationSettings(BaseModel):
    """Разделение записи по говорящим (sherpa-onnx).

    Выключено по умолчанию: это +34 МБ моделей и заметное время сверх
    транскрибации, а нужно оно далеко не каждой записи.
    """

    enabled: bool = False
    # 0 = определить число говорящих автоматически (по threshold). Если оно
    # известно заранее — указать явно: результат заметно лучше, чем у
    # авто-кластеризации.
    num_speakers: int = Field(default=0, ge=0, le=20)
    # Порог кластеризации при num_speakers=0. Меньше — больше говорящих.
    threshold: float = Field(default=0.5, ge=0.1, le=1.5)
    # Модель голосовых эмбеддингов — имя файла из релизов sherpa-onnx (список
    # см. backend/asr/diarize.py: EMBEDDING_MODELS). Тип str, а не Literal:
    # набор моделей в апстриме пополняется, а неизвестное имя безопасно
    # превращается в ошибку скачивания с понятным текстом.
    embedding_model: str = "wespeaker_en_voxceleb_resnet34_LM.onnx"
    # Потоков CPU для onnxruntime. 0 = по числу ядер: sherpa-onnx по умолчанию
    # берёт ровно один поток, и на многоядерной машине это кратно дольше
    # (те же грабли, что были у CTranslate2 в Whisper).
    num_threads: int = Field(default=0, ge=0, le=256)
    # Шаг окна сегментации (доля от окна). Главный регулятор «скорость против
    # точности»: 0.1 — как в pyannote (каждый участок просматривается 10 раз),
    # 0.3 — втрое быстрее. На замерах (см. README) 0.1 и 0.3 давали одинаковую
    # разметку, а 0.5 уже разваливал кластеризацию, поэтому по умолчанию 0.3.
    window_shift_ratio: float = Field(default=0.3, ge=0.05, le=0.9)


class ASRSettings(BaseModel):
    engine: Literal["gigaam", "whisper"] = "gigaam"
    language: str = "ru"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    gigaam: GigaamSettings = GigaamSettings()
    whisper: WhisperSettings = WhisperSettings()
    # Диаризация не привязана к движку: она работает по таймкодам слов,
    # которые отдают оба.
    diarization: DiarizationSettings = DiarizationSettings()


class PromptTemplate(BaseModel):
    system: str = ""
    user_template: str = ""


class PromptsSettings(BaseModel):
    youtube_description: PromptTemplate = PromptTemplate()
    youtube_timecodes: PromptTemplate = PromptTemplate()
    telegram_post: PromptTemplate = PromptTemplate()
    article: PromptTemplate = PromptTemplate()
    custom_system: str = ""
    # Промпт LLM-нормализации текста перед озвучиванием (пусто = встроенный).
    tts_normalize: PromptTemplate = PromptTemplate()


class AppSettings(BaseModel):
    title: str = "Записная книжка"
    port: int = 8001
    theme: Literal["dark", "light"] = "dark"


# ---------- TTS (озвучивание текста) ----------


class SileroSettings(BaseModel):
    version: Literal["v4_ru", "v3_1_ru"] = "v4_ru"
    speaker: Literal["aidar", "baya", "kseniya", "xenia", "eugene"] = "baya"
    sample_rate: Literal[8000, 24000, 48000] = 48000
    put_accent: bool = True
    put_yo: bool = True


class PiperSettings(BaseModel):
    # speaker — имя голоса rhasspy/piper-voices (тип str: список голосов может
    # расширяться). length_scale > 1 — медленнее (для размеренного чтения).
    speaker: str = "ru_RU-ruslan-medium"
    length_scale: float = 1.0


class YandexSettings(BaseModel):
    # Облако: Яндекс SpeechKit. api_key + folder_id из консоли Yandex Cloud.
    # Секреты — plaintext в settings.json (локальное однопользовательское приложение).
    # voice — имя голоса из каталога SpeechKit; emotion — эмоциональная окраска.
    api_key: str = ""
    folder_id: str = ""
    voice: str = "alena"
    emotion: Literal["neutral", "good", "evil"] = "neutral"


class SberSettings(BaseModel):
    # Облако: Сбер SaluteSpeech. client_id/client_secret — из дев-портала SberDevices
    # (developers.sber.ru); обмениваются на короткоживущий access-token в рантайме
    # (OAuth client_credentials, ~24ч). voice — из каталога SaluteSpeech.
    # ВАЖНО: API Сбера — под сертификатом российского центра Минцифры (не в certifi);
    # см. backend.tts.engine_sber.
    client_id: str = ""
    client_secret: str = ""
    voice: str = "Nazar"


class EdgeSettings(BaseModel):
    # Облако: Microsoft Edge neural TTS через пакет edge-tts. Без API-ключа и без
    # регистрации — публичный endpoint «Читать вслух». voice — ru-RU-* neural.
    voice: str = "ru-RU-DmitryNeural"


class TTSPauses(BaseModel):
    sentence: int = 300
    paragraph: int = 700
    chapter: int = 1500


class TTSNormalize(BaseModel):
    # use_llm=False → rule-based (num2words). True → LLM по профилям из llm.profiles.
    use_llm: bool = False


class TTSAccent(BaseModel):
    # Расстановка ударений ruaccent перед синтезом (формат «+» понимает Silero).
    # model_size — омограф-модель ruaccent (tiny бережёт ОЗУ). Тип str, а не
    # Literal: набор размеров зависит от версии ruaccent, неизвестное значение
    # безопасно откатывается на отсутствие ударений (см. backend.tts.stress).
    enabled: bool = True
    model_size: str = "tiny"


class TTSExport(BaseModel):
    format: Literal["mp3", "m4b", "m4a", "wav"] = "mp3"
    split_chapters: bool = True
    bitrate: int = 128000


class TTSSettings(BaseModel):
    engine: Literal["silero", "piper", "yandex", "sber", "edge"] = "silero"
    language: str = "ru"
    device: Literal["auto", "cpu", "cuda"] = "cpu"
    silero: SileroSettings = SileroSettings()
    piper: PiperSettings = PiperSettings()
    yandex: YandexSettings = YandexSettings()
    sber: SberSettings = SberSettings()
    edge: EdgeSettings = EdgeSettings()
    pauses: TTSPauses = TTSPauses()
    # Резать по предложениям → пауза pauses.sentence после каждого предложения
    # (а не после пачки). Чуть медленнее синтез, зато речь размереннее.
    pause_each_sentence: bool = False
    normalize: TTSNormalize = TTSNormalize()
    accent: TTSAccent = TTSAccent()
    export: TTSExport = TTSExport()
    chapter_pattern: str = ""  # пусто = встроенный паттерн глав


class Settings(BaseModel):
    app: AppSettings = AppSettings()
    asr: ASRSettings = ASRSettings()
    llm: LLMSettings = LLMSettings()
    prompts: PromptsSettings = PromptsSettings()
    tts: TTSSettings = TTSSettings()
