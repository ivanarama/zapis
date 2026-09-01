"""Шаблоны промптов: дефолты в коде, кастомизация в settings.json под `prompts.*`."""

from __future__ import annotations

from typing import Literal

from ..config import get_settings
from ..formats import speaker_label
from ..schema import PromptTemplate

PresetKey = Literal["youtube_description", "youtube_timecodes", "telegram_post", "article"]
PRESET_KEYS: tuple[PresetKey, ...] = (
    "youtube_description",
    "youtube_timecodes",
    "telegram_post",
    "article",
)

_DEFAULTS: dict[str, PromptTemplate] = {
    "youtube_description": PromptTemplate(
        system=(
            "Ты — помощник для создания описаний к видео. "
            "Создаёшь краткое информативное описание на русском языке "
            "и список основных тем по транскрипту."
        ),
        user_template=(
            "На основе транскрипта видео:\n"
            "1. Сформулируй краткое описание (2–3 предложения)\n"
            "2. Перечисли 3–5 основных тем\n\n"
            "Транскрипт:\n{transcript}\n\n"
            "Формат:\nОПИСАНИЕ:\n<...>\n\nТЕМЫ:\n- ...\n- ..."
        ),
    ),
    "youtube_timecodes": PromptTemplate(
        system=(
            "Ты — помощник для создания таймкодов к видео. "
            "Каждый таймкод соответствует началу новой темы или раздела."
        ),
        user_template=(
            "На основе сегментов с временными метками собери таймкоды для описания YouTube. "
            "Используй формат `M:SS — Название раздела`, не более 15–20 пунктов.\n\n"
            "Сегменты:\n{segments}\n\n"
            "Список таймкодов:"
        ),
    ),
    "telegram_post": PromptTemplate(
        system=(
            "Ты — помощник для создания постов в Telegram. "
            "Делаешь яркие, понятные посты с эмодзи и форматированием."
        ),
        user_template=(
            "На основе транскрипта создай пост для Telegram-канала.\n"
            "- информативный\n- с эмодзи\n- с жирными выделениями ключевых мыслей\n"
            "- с лёгким призывом к обсуждению, если уместно\n\n"
            "Транскрипт:\n{transcript}\n\nГотовый пост:"
        ),
    ),
    "article": PromptTemplate(
        system=(
            "Ты — помощник для написания статей. "
            "Создаёшь структурированную статью с заголовками."
        ),
        user_template=(
            "На основе транскрипта напиши статью на русском языке.\n"
            "Структура: введение, 2–4 раздела с заголовками, заключение. "
            "Стиль: ясный, без воды.\n\n"
            "Транскрипт:\n{transcript}\n\nСтатья:"
        ),
    ),
}

_DEFAULT_CUSTOM_SYSTEM = (
    "Ты — помощник, который работает с расшифровкой видео или аудио. "
    "Отвечай на русском языке, опираясь на предоставленный транскрипт."
)


def default_prompts() -> dict:
    return {
        **{k: v.model_dump() for k, v in _DEFAULTS.items()},
        "custom_system": _DEFAULT_CUSTOM_SYSTEM,
    }


def get_prompts() -> dict[str, PromptTemplate | str]:
    """Возвращает действующие шаблоны: пользовательские поверх дефолтных."""
    s = get_settings().prompts
    out: dict[str, PromptTemplate | str] = {}
    for key in PRESET_KEYS:
        user_tmpl: PromptTemplate = getattr(s, key)
        # Если поля пустые — берём дефолт.
        merged = PromptTemplate(
            system=(user_tmpl.system or _DEFAULTS[key].system),
            user_template=(user_tmpl.user_template or _DEFAULTS[key].user_template),
        )
        out[key] = merged
    out["custom_system"] = (s.custom_system or _DEFAULT_CUSTOM_SYSTEM)
    return out


def _format_segments_for_timecodes(segments: list[dict], limit: int = 120) -> str:
    """Форматирует сегменты для генерации таймкодов.

    Равномерно выбирает *limit* сегментов по всей длительности,
    чтобы LLM видел контент от начала до конца, а не только первые минуты.
    """
    if not segments:
        return ""

    if len(segments) <= limit:
        picked = segments
    else:
        # Равномерная выборка: берём каждый n-й сегмент
        step = len(segments) / limit
        picked = [segments[int(i * step)] for i in range(limit)]

    lines = []
    for seg in picked:
        start = seg.get("start") or 0.0
        text = (seg.get("text") or "").strip().replace("\n", " ")
        if not text:
            continue
        # Если расшифровка размечена по говорящим, подпись идёт в строку:
        # смена говорящего — сильный сигнал границы темы для таймкодов.
        speaker = seg.get("speaker")
        if speaker is not None:
            text = f"{speaker_label(speaker)}: {text}"
        lines.append(f"{_mmss(start)} {text[:140]}")
    return "\n".join(lines)


def _mmss(seconds: float) -> str:
    if seconds >= 3600:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h}:{m:02d}:{s:02d}"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def _safe_substitute(template: str, **kwargs: str) -> tuple[str, set[str]]:
    """Безопасная подстановка плейсхолдеров {key} → value.

    Возвращает (результат, множество использованных ключей).
    В отличие от str.format(), не падает на фигурных скобках в тексте
    пользователя и не требует, чтобы все ключи были указаны в шаблоне.
    """
    used: set[str] = set()
    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        if placeholder in template:
            template = template.replace(placeholder, value)
            used.add(key)
    return template, used


def build_messages_for_preset(
    preset: PresetKey,
    transcript_text: str,
    segments: list[dict],
) -> list[dict]:
    prompts = get_prompts()
    tmpl: PromptTemplate = prompts[preset]  # type: ignore[assignment]

    if preset == "youtube_timecodes":
        seg_text = _format_segments_for_timecodes(segments)
        user, used = _safe_substitute(
            tmpl.user_template, segments=seg_text,
        )
        # Если шаблон не содержал {segments} — добавляем в конец
        if "segments" not in used and seg_text:
            user += f"\n\nСегменты:\n{seg_text}"
    else:
        # Ограничение на длину текста: современные модели (gpt-4o, claude, etc.)
        # поддерживают 128k+ токенов контекста. 64k символов ≈ 16k токенов —
        # достаточно для 30–60 мин транскрипта без разорванности.
        trunc = transcript_text[:64000]
        user, used = _safe_substitute(
            tmpl.user_template, transcript=trunc,
        )
        # Если шаблон не содержал {transcript} — добавляем в конец
        if "transcript" not in used and trunc:
            user += f"\n\nТранскрипт:\n{trunc}"

    return [
        {"role": "system", "content": tmpl.system},
        {"role": "user", "content": user},
    ]


def build_messages_for_custom(
    transcript_text: str,
    user_question: str,
) -> list[dict]:
    prompts = get_prompts()
    system = prompts["custom_system"]  # type: ignore[assignment]
    user = (
        f"Транскрипт:\n{transcript_text[:64000]}\n\n"
        f"Задание: {user_question.strip()}"
    )
    return [
        {"role": "system", "content": str(system)},
        {"role": "user", "content": user},
    ]
