"""P7 — text-to-speech synthesis.

Provider-agnostic TTS via OpenRouter's audio output modality
(``modalities: ["audio"]`` + ``audio: {voice, format}``). Default model is
``fish-audio/s2.1-pro-free:free`` so the feature works out of the box with
just OPENROUTER_API_KEY. Mirrors the Whisper STT provider pattern
(whisper_stt.py): provider + model are server-side settings, the request is a
plain httpx call, and the response is a base64 audio blob.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Optional

import httpx

from . import config as app_config

logger = logging.getLogger(__name__)

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = "fish-audio/s2.1-pro-free:free"
DEFAULT_FORMAT = "mp3"
VALID_PROVIDERS = ("openrouter",)

# Voice ids are provider-specific opaque strings; keep them conservative.
VOICE_RE = re.compile(r"^[A-Za-z0-9._:/-]{0,128}$")

TTS_TIMEOUT = 120.0


def _get_settings():
    return app_config.settings


def resolve_provider() -> str:
    raw = (getattr(_get_settings(), "tts_provider", None) or DEFAULT_PROVIDER).strip().lower()
    if raw not in VALID_PROVIDERS:
        return DEFAULT_PROVIDER
    return raw


def resolve_model() -> str:
    return (getattr(_get_settings(), "tts_openrouter_model", None) or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def resolve_default_voice() -> Optional[str]:
    voice = getattr(_get_settings(), "tts_voice", None)
    return voice.strip() if voice and voice.strip() else None


def validate_voice(voice: str) -> bool:
    return bool(VOICE_RE.match(voice or ""))


def _extract_audio_base64(data: dict) -> Optional[str]:
    """Pull the base64 audio out of an OpenRouter chat completion response.

    Primary shape: ``choices[0].message.audio.data``. Fallback: content parts
    with ``type == "audio"`` (OpenAI-style multimodal audio parts)."""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    audio = message.get("audio") or {}
    if isinstance(audio, dict) and audio.get("data"):
        return str(audio["data"])
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "audio":
                inner = part.get("audio") or {}
                if isinstance(inner, dict) and inner.get("data"):
                    return str(inner["data"])
    return None


def synthesize_speech(text: str, voice: Optional[str] = None) -> bytes:
    """Synthesize speech for ``text`` and return the raw audio bytes (mp3).

    Synchronous/blocking (httpx) — call via asyncio.to_thread from the API.
    Raises RuntimeError with a safe-to-surface message on failure."""
    provider = resolve_provider()
    model = resolve_model()

    if provider != "openrouter":
        raise RuntimeError(f"Unsupported TTS provider: {provider}")

    api_key = getattr(_get_settings(), "openrouter_api_key", None)
    if not api_key:
        raise RuntimeError("OpenRouter API key is not configured (set OPENROUTER_API_KEY)")

    effective_voice = voice or resolve_default_voice()
    if effective_voice and not validate_voice(effective_voice):
        raise RuntimeError("Invalid voice id")

    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "modalities": ["audio"],
        "audio": {"format": DEFAULT_FORMAT},
        # Scale the audio budget with input length so long replies aren't
        # silently truncated, capped well below the provider's hard limit.
        "max_tokens": max(2048, min(len(text) * 2, 8192)),
    }
    if effective_voice:
        payload["audio"]["voice"] = effective_voice

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=TTS_TIMEOUT) as client:
            resp = client.post(OPENROUTER_CHAT_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"TTS connection error: {str(exc)[:200]}")

    if resp.status_code != 200:
        raise RuntimeError(f"TTS error ({resp.status_code}): {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError("TTS returned invalid JSON")

    audio_b64 = _extract_audio_base64(data)
    if not audio_b64:
        raise RuntimeError("TTS response contained no audio output")

    try:
        return base64.b64decode(audio_b64)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"TTS audio payload is corrupt: {exc}")
