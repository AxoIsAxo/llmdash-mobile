from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

AUDIO_MODEL_PATTERNS = []
AUDIO_MODEL_LOWERS = []
_RUNTIME_AUDIO_CACHE: dict[tuple[str, str], bool] = {}


def _probe_audio_support(base_url: str, api_key: str, model_name: str) -> bool:
    import base64 as _b64
    import struct
    sample_rate = 16000
    pcm = b"\x00\x00"
    byte_rate = sample_rate * 1 * 2
    data_size = len(pcm)
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, sample_rate, byte_rate, 2, 16)
    data = struct.pack("<4sI", b"data", data_size) + pcm
    riff = struct.pack("<4sI", b"RIFF", 4 + len(fmt) + len(data)) + b"WAVE"
    wav = riff + fmt + data
    b64 = _b64.b64encode(wav).decode("ascii")

    async def _do_probe():
        async with httpx.AsyncClient(timeout=8) as client:
            return await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "ping"},
                            {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
                        ],
                    }],
                    "max_tokens": 1,
                },
            )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, _do_probe())
                resp = fut.result(timeout=10)
        else:
            resp = asyncio.run(_do_probe())
    except Exception:
        return False

    if resp.status_code < 400:
        return True
    error_text = (resp.text or "").lower()
    audio_keywords = (
        "audio", "input_audio_format", "invalid_audio",
        "audio format", "audio data", "audio input",
        "audio content", "invalid audio",
    )
    unsupported_keywords = (
        "unsupported", "not supported", "does not support",
        "is not supported",
    )
    if any(k in error_text for k in audio_keywords) and not any(k in error_text for k in unsupported_keywords):
        return True
    return False


async def detect_audio_enabled(model, app_config) -> bool:
    audio_enabled = bool(getattr(model, "audio_enabled", False))
    if (
        not audio_enabled
        and getattr(model, "provider", None) == "openai_compatible"
        and getattr(model, "base_url", None)
        and getattr(model, "api_key_env", None)
    ):
        try:
            from . import config as _app_config
            cache_key = (model.base_url.rstrip("/"), model.model_name)
            cached = _RUNTIME_AUDIO_CACHE.get(cache_key)
            if cached is None:
                api_key = getattr(app_config.settings, model.api_key_env.lower(), None) or ""
                detected = False
                arch_summary = "no_response"
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{cache_key[0]}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    raw_list = data.get("data", data.get("models", []))
                    for m in raw_list:
                        mid = m.get("id", m.get("name", ""))
                        if mid == cache_key[1] or mid.endswith(f"/{cache_key[1]}"):
                            arch = m.get("architecture", {}) or {}
                            in_mods = [str(x).lower() for x in (arch.get("input_modalities", []) or arch.get("modalities", []) or [])]
                            arch_summary = f"input_modalities={in_mods}"
                            if "audio" in in_mods or bool(m.get("input_audio")) or bool(m.get("supports_audio")):
                                detected = True
                            break
                else:
                    arch_summary = f"http_{resp.status_code}"
                if not detected and resp.status_code == 200 and api_key:
                    probe = _probe_audio_support(cache_key[0], api_key, model.model_name)
                    detected = probe
                    arch_summary += f" probe={probe}"
                _RUNTIME_AUDIO_CACHE[cache_key] = detected
                cached = detected
                logger.info(
                    f"audio autodetect: model={model.model_name} detected={detected} ({arch_summary})"
                )
            if cached:
                audio_enabled = True
        except Exception as e:
            logger.debug(f"audio runtime autodetect failed for {model.model_name}: {e}")
    return audio_enabled
