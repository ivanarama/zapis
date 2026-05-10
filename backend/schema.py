"""Pydantic-схема settings.json.

LLM-блок построен по образцу localChat: список профилей с порядком = приоритет
fallback. Внутри профиля список моделей — тоже с приоритетом."""

from __future__ import annotations

from typing import Any, Literal

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


class WhisperSettings(BaseModel):
    model: Literal["tiny", "base", "small", "medium", "large-v2", "large-v3"] = "small"


class ASRSettings(BaseModel):
    engine: Literal["gigaam", "whisper"] = "gigaam"
    language: str = "ru"
    gigaam: GigaamSettings = GigaamSettings()
    whisper: WhisperSettings = WhisperSettings()


class PromptTemplate(BaseModel):
    system: str = ""
    user_template: str = ""


class PromptsSettings(BaseModel):
    youtube_description: PromptTemplate = PromptTemplate()
    youtube_timecodes: PromptTemplate = PromptTemplate()
    telegram_post: PromptTemplate = PromptTemplate()
    article: PromptTemplate = PromptTemplate()
    custom_system: str = ""


class AppSettings(BaseModel):
    title: str = "Записная книжка"
    port: int = 8001
    theme: Literal["dark", "light"] = "dark"


class Settings(BaseModel):
    app: AppSettings = AppSettings()
    asr: ASRSettings = ASRSettings()
    llm: LLMSettings = LLMSettings()
    prompts: PromptsSettings = PromptsSettings()
