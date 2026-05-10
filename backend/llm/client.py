"""LLM-клиент с мульти-профилями и SSE-стримингом.

Адаптировано из localChat/backend/llm_client.py: AsyncOpenAI + AsyncAnthropic,
fallback по профилям и моделям, обработка типичных ошибок API."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator

import httpx
from openai import APIStatusError, AsyncOpenAI, AuthenticationError

from ..config import get_settings

log = logging.getLogger("zapis.llm")

_AUTH_HINT = (
    "Неверный или просроченный API-ключ (401). "
    "Проверьте ключ в настройках LLM-профиля."
)

_BAD_REQUEST_HINT = (
    "Запрос отклонён API (400). Частые причины: неверная модель для этого URL, "
    "слишком длинный контекст, превышен max_tokens."
)


def format_llm_user_error(exc: BaseException) -> str:
    if isinstance(exc, AuthenticationError):
        return _AUTH_HINT
    code = getattr(exc, "status_code", None)
    if isinstance(exc, APIStatusError) and code == 401:
        return _AUTH_HINT
    if isinstance(exc, APIStatusError) and code == 400:
        return f"{_BAD_REQUEST_HINT}\nДетали: {exc}"
    text = str(exc)
    if "401" in text and ("invalid_api_key" in text or "token" in text.lower()):
        return _AUTH_HINT
    if "400" in text and "invalid_parameter" in text:
        return f"{_BAD_REQUEST_HINT}\nДетали: {text}"
    return text


def _api_key_from_env() -> str:
    for name in (
        "OPENAI_API_KEY",
        "LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "DASHSCOPE_API_KEY",
    ):
        v = (os.getenv(name) or "").strip()
        if v:
            return v
    return ""


def _key_for_profile(profile_key: str, global_key: str) -> str:
    return (profile_key or "").strip() or (global_key or "").strip() or _api_key_from_env()


def _make_openai_client(base_url: str, api_key: str) -> AsyncOpenAI:
    if not api_key:
        raise ValueError("Пустой API-ключ для LLM-профиля.")
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=30.0),
        trust_env=False,
    )
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        http_client=http_client,
    )


def _max_completion_tokens(raw: int) -> int:
    return max(1, min(int(raw), 8192))


async def _stream_openai(
    client: AsyncOpenAI,
    model_name: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    response = await client.chat.completions.create(
        model=model_name.strip(),
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    got_chunk = False
    async for chunk in response:
        got_chunk = True
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
        else:
            extra = getattr(delta, "__dict__", {}) if delta else {}
            for field in ("reasoning_content", "reasoning", "thinking"):
                val = extra.get(field) or getattr(delta, field, None)
                if val:
                    yield val
                    break
    if not got_chunk:
        log.warning("LLM stream без чанков для модели %s", model_name)


def _messages_for_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if role == "system":
            system_parts.append(content)
            continue
        if role not in ("user", "assistant"):
            role = "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"] = f"{out[-1]['content']}\n\n{content}"
        else:
            out.append({"role": role, "content": content})
    if not out:
        out = [{"role": "user", "content": "."}]
    return ("\n\n".join(system_parts) if system_parts else ""), out


async def _stream_anthropic(
    model_name: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    *,
    base_url: str,
    api_key: str,
) -> AsyncGenerator[str, None]:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("Установите пакет anthropic: pip install anthropic") from e
    if not api_key:
        raise ValueError("Пустой API-ключ для Anthropic-профиля.")

    system, amsg = _messages_for_anthropic(messages)
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0, connect=30.0),
        trust_env=False,
    )
    client = anthropic.AsyncAnthropic(
        api_key=api_key, base_url=base_url.rstrip("/"), http_client=http_client,
    )
    kwargs: dict = {
        "model": model_name.strip(),
        "max_tokens": max_tokens,
        "messages": amsg,
        "temperature": temperature,
    }
    if system:
        kwargs["system"] = system
    async with client.messages.stream(**kwargs) as stream:
        async for text in stream.text_stream:
            yield text


async def stream_chat(messages: list[dict]) -> AsyncGenerator[str, None]:
    """Стримит ответ LLM, перебирая профили в порядке приоритета.

    Если первый профиль/модель упали с ошибкой ДО первого чанка — пробуем
    следующие. Если стрим уже начался и упал — пробрасываем ошибку, чтобы
    клиент видел реальное обрывание (а не «волшебное» переключение в середине
    ответа)."""
    s = get_settings().llm
    if not s.profiles:
        raise RuntimeError("Не настроено ни одного LLM-профиля. Откройте настройки.")

    last_exc: BaseException | None = None
    max_tok = _max_completion_tokens(s.max_tokens)

    for pi, prof in enumerate(s.profiles):
        url = (prof.base_url or s.base_url or "").strip().rstrip("/")
        key = _key_for_profile(prof.api_key, s.api_key)
        if not key:
            last_exc = last_exc or ValueError(f"Профиль «{prof.name}»: пустой ключ.")
            continue
        if not url:
            last_exc = last_exc or ValueError(f"Профиль «{prof.name}»: пустой Base URL.")
            continue

        for mi, mn in enumerate(prof.models):
            mn = mn.strip()
            if not mn:
                continue
            streamed_any = False
            try:
                if prof.api_provider == "anthropic":
                    gen = _stream_anthropic(
                        mn, messages, s.temperature, max_tok,
                        base_url=url, api_key=key,
                    )
                else:
                    client = _make_openai_client(url, key)
                    gen = _stream_openai(client, mn, messages, s.temperature, max_tok)
                async for token in gen:
                    streamed_any = True
                    yield token
                if not streamed_any:
                    last_exc = last_exc or RuntimeError(
                        f"Модель {mn} вернула пустой стрим."
                    )
                    continue
                return
            except Exception as exc:
                if streamed_any:
                    raise
                last_exc = exc
                log.warning(
                    "LLM: профиль «%s» / модель %s — %s; пробуем дальше",
                    prof.name, mn, exc,
                )

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Не удалось вызвать LLM ни по одному профилю.")
