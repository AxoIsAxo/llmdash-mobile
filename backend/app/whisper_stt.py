import io
import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

_whisper_model = None
_loaded_model_size = None
_loaded_compute_type = None
_loaded_device = None

VALID_MODEL_SIZES = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "distil-large-v3",
)
DEFAULT_MODEL_SIZE = "small"
VALID_COMPUTE_TYPES = (
    "int8",
    "int8_float16",
    "int16",
    "float16",
    "float32",
    "bfloat16",
)
DEFAULT_COMPUTE_TYPE = "int8"

VALID_PROVIDERS = ("local", "openrouter")
DEFAULT_PROVIDER = "local"
DEFAULT_OPENROUTER_MODEL = "openai/whisper-1"
OPENROUTER_TRANSCRIPTIONS_URL = "https://openrouter.ai/api/v1/audio/transcriptions"


def _get_settings():
    from . import config as app_config
    return app_config.settings


def _resolve_model_size() -> str:
    raw = getattr(_get_settings(), "whisper_model", DEFAULT_MODEL_SIZE) or DEFAULT_MODEL_SIZE
    raw = str(raw).strip().lower()
    if raw == "tiny.en" or raw == "tiny":
        candidate = "tiny"
    elif raw == "base.en" or raw == "base":
        candidate = "base"
    elif raw == "small.en" or raw == "small":
        candidate = "small"
    elif raw == "medium.en" or raw == "medium":
        candidate = "medium"
    elif raw in ("large", "large-v2", "large-v1"):
        candidate = "large-v3"
    else:
        candidate = raw
    if candidate not in VALID_MODEL_SIZES:
        logger.warning(f"Unknown whisper model '{raw}', falling back to '{DEFAULT_MODEL_SIZE}'")
        candidate = DEFAULT_MODEL_SIZE
    return candidate


def _resolve_compute_type() -> str:
    raw = getattr(_get_settings(), "whisper_compute_type", DEFAULT_COMPUTE_TYPE) or DEFAULT_COMPUTE_TYPE
    raw = str(raw).strip().lower()
    if raw not in VALID_COMPUTE_TYPES:
        return DEFAULT_COMPUTE_TYPE
    return raw


def _resolve_device() -> str:
    raw = getattr(_get_settings(), "whisper_device", "auto")
    if raw in (None, "", "auto"):
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                return "cuda"
        except Exception:
            pass
        return "cpu"
    return str(raw).lower()


def _resolve_beam_size() -> int:
    try:
        n = int(getattr(_get_settings(), "whisper_beam_size", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, 10))


def get_model():
    global _whisper_model, _loaded_model_size, _loaded_compute_type, _loaded_device
    size = _resolve_model_size()
    compute_type = _resolve_compute_type()
    device = _resolve_device()

    if device == "cuda" and compute_type in ("int8", "int16"):
        if "float16" not in compute_type:
            compute_type = f"{compute_type}_float16"

    if (
        _whisper_model is None
        or _loaded_model_size != size
        or _loaded_compute_type != compute_type
        or _loaded_device != device
    ):
        from faster_whisper import WhisperModel
        logger.info(
            f"Loading faster-whisper model: {size} (device={device}, compute_type={compute_type})"
        )
        _whisper_model = WhisperModel(
            size,
            device=device,
            compute_type=compute_type,
        )
        _loaded_model_size = size
        _loaded_compute_type = compute_type
        _loaded_device = device
        logger.info(f"faster-whisper model '{size}' ready")
    return _whisper_model


def _decode_audio(audio_bytes: bytes):
    from faster_whisper.audio import decode_audio
    return decode_audio(io.BytesIO(audio_bytes), sampling_rate=16000)


def transcribe_audio(audio_bytes: bytes) -> str:
    if not audio_bytes:
        return ""
    model = get_model()
    audio = _decode_audio(audio_bytes)

    language = getattr(_get_settings(), "whisper_language", None) or None
    beam_size = _resolve_beam_size()
    common = dict(
        language=language,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        without_timestamps=True,
        condition_on_previous_text=False,
    )

    def _run(src):
        try:
            segs, info = model.transcribe(src, **common)
            if not info.all_language_probs:
                return ""
            text = " ".join(seg.text.strip() for seg in segs if seg.text).strip()
            logger.debug(
                f"Transcribed {len(audio_bytes)}B audio (lang={info.language}, "
                f"prob={info.language_probability:.2f}, duration={info.duration:.1f}s)"
            )
            return text
        except ValueError as e:
            if "max() iterable argument is empty" in str(e):
                logger.debug("No speech detected (VAD stripped all audio)")
                return ""
            raise

    try:
        return _run(audio)
    except Exception:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            return _run(tmp_path)
        except Exception as e:
            logger.warning(f"Transcription failed: {e}")
            return ""
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _resolve_provider() -> str:
    raw = getattr(_get_settings(), "whisper_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER
    raw = str(raw).strip().lower()
    if raw not in VALID_PROVIDERS:
        logger.warning(f"Unknown whisper provider '{raw}', falling back to '{DEFAULT_PROVIDER}'")
        return DEFAULT_PROVIDER
    return raw


def _resolve_openrouter_model() -> str:
    raw = getattr(_get_settings(), "whisper_openrouter_model", DEFAULT_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL
    return str(raw).strip() or DEFAULT_OPENROUTER_MODEL


def _resolve_openrouter_language() -> Optional[str]:
    raw = getattr(_get_settings(), "whisper_language", None)
    if raw is None:
        return None
    raw = str(raw).strip()
    return raw or None


def transcribe_audio_openrouter(audio_bytes: bytes, filename: str = "audio.webm", content_type: str = "audio/webm") -> str:
    """Transcribe audio using the OpenRouter /audio/transcriptions API.

    The OpenRouter endpoint is OpenAI-compatible and accepts multipart form data
    with `file` + `model`. Authentication uses the OPENROUTER_API_KEY env var
    that the app already configures.
    """
    if not audio_bytes:
        return ""
    api_key = getattr(_get_settings(), "openrouter_api_key", None)
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    model = _resolve_openrouter_model()
    language = _resolve_openrouter_language()

    import httpx

    files = {"file": (filename, audio_bytes, content_type)}
    data = {"model": model}
    if language:
        data["language"] = language

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                OPENROUTER_TRANSCRIPTIONS_URL,
                headers=headers,
                files=files,
                data=data,
            )
    except httpx.HTTPError as e:
        logger.warning(f"OpenRouter transcription request failed: {e}")
        raise RuntimeError(f"OpenRouter request failed: {e}") from e

    if resp.status_code >= 400:
        body_snippet = (resp.text or "")[:300]
        logger.warning(
            f"OpenRouter transcription HTTP {resp.status_code}: {body_snippet}"
        )
        raise RuntimeError(
            f"OpenRouter returned HTTP {resp.status_code}: {body_snippet}"
        )

    try:
        payload = resp.json()
    except ValueError as e:
        raise RuntimeError(f"OpenRouter returned non-JSON response: {e}") from e

    text = ""
    if isinstance(payload, dict):
        text = payload.get("text", "") or ""
        if not text and "segments" in payload:
            segs = payload.get("segments") or []
            text = " ".join(str(seg.get("text", "")) for seg in segs if isinstance(seg, dict)).strip()
    text = str(text).strip()
    logger.debug(
        f"OpenRouter transcribed {len(audio_bytes)}B audio (model={model}, len={len(text)})"
    )
    return text
