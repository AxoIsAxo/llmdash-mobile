import os
import secrets
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    database_path: str = "data/llmdash.db"
    jwt_secret: Optional[str] = None
    registration_enabled: bool = False
    ip_account_limit: int = 3
    deepseek_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    minimax_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    searxng_url: str = "http://localhost:8080"
    lnbits_url: Optional[str] = None
    lnbits_invoice_key: Optional[str] = None
    file_upload_enabled: bool = True
    uploads_dir: str = "data/uploads"
    documents_dir: str = "data/documents"
    ocr_enabled: bool = True
    ocr_strategy: str = "ocr"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_device: str = "auto"
    whisper_language: Optional[str] = None
    whisper_beam_size: int = 1
    whisper_provider: str = "local"
    whisper_openrouter_model: str = "openai/whisper-1"

    # P7 text-to-speech (OpenRouter audio output; gpt-audio-mini default)
    tts_provider: str = "openrouter"
    # NOTE: fish-audio has no audio-output endpoint on OpenRouter (404), so the
    # default is OpenAI's cheap audio model; override via TTS_OPENROUTER_MODEL.
    tts_openrouter_model: str = "openai/gpt-audio-mini"
    tts_voice: Optional[str] = "alloy"  # OpenAI voice id (alloy/ash/...)

    # Cross-session memory
    memory_dir: str = "data/memory"
    memory_enabled: bool = True
    memory_extract_model: str = ""  # empty = auto (user's most recent model)
    memory_extract_min_turns: int = 10
    memory_extract_batch_chars: int = 40000

    # Extrovert OIDC login
    extrovert_client_id: str = ""
    extrovert_client_secret: str = ""
    extrovert_issuer: str = "https://extrovert.redforged.eu"
    extrovert_redirect_uri: str = ""  # override the auto-derived callback URL
    extrovert_allow_signup: bool = True  # create new accounts on first login

    # P4 agentic git: fixed commit author (never the user's git identity)
    git_bot_name: str = "LLMDash"
    git_bot_email: str = ""  # empty -> llmdash@<clone host> fallback

    model_config = {"env_file": "data/.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


ENV_VAR_MAP = {
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "MINIMAX_API_KEY": "minimax_api_key",
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "LNBITS_URL": "lnbits_url",
    "LNBITS_INVOICE_KEY": "lnbits_invoice_key",
    "JWT_SECRET": "jwt_secret",
    "FILE_UPLOAD_ENABLED": "file_upload_enabled",
    "OCR_ENABLED": "ocr_enabled",
    "OCR_STRATEGY": "ocr_strategy",
    "WHISPER_MODEL": "whisper_model",
    "WHISPER_COMPUTE_TYPE": "whisper_compute_type",
    "WHISPER_DEVICE": "whisper_device",
    "WHISPER_LANGUAGE": "whisper_language",
    "WHISPER_BEAM_SIZE": "whisper_beam_size",
    "WHISPER_PROVIDER": "whisper_provider",
    "WHISPER_OPENROUTER_MODEL": "whisper_openrouter_model",
    "TTS_PROVIDER": "tts_provider",
    "TTS_OPENROUTER_MODEL": "tts_openrouter_model",
    "TTS_VOICE": "tts_voice",
    "MEMORY_DIR": "memory_dir",
    "MEMORY_ENABLED": "memory_enabled",
    "MEMORY_EXTRACT_MODEL": "memory_extract_model",
    "MEMORY_EXTRACT_MIN_TURNS": "memory_extract_min_turns",
    "MEMORY_EXTRACT_BATCH_CHARS": "memory_extract_batch_chars",
    "EXTROVERT_CLIENT_ID": "extrovert_client_id",
    "EXTROVERT_CLIENT_SECRET": "extrovert_client_secret",
    "EXTROVERT_ISSUER": "extrovert_issuer",
    "EXTROVERT_REDIRECT_URI": "extrovert_redirect_uri",
    "EXTROVERT_ALLOW_SIGNUP": "extrovert_allow_signup",
    "GIT_BOT_NAME": "git_bot_name",
    "GIT_BOT_EMAIL": "git_bot_email",
}


def reload_settings():
    global settings
    settings = Settings()


_jwt_secret: Optional[str] = None


def get_jwt_secret() -> str:
    """Return the JWT signing secret.

    Priority: env var / data/.env (JWT_SECRET) -> persisted file next to the
    database (data/jwt_secret). The persisted file ensures the secret is
    stable across restarts and across multiple workers, so sessions survive
    restarts and multi-worker deployments share one secret.
    """
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret
    if settings.jwt_secret:
        _jwt_secret = settings.jwt_secret
        return _jwt_secret

    secret_file = os.path.join(
        os.path.dirname(os.path.abspath(settings.database_path)) or ".",
        "jwt_secret",
    )
    os.makedirs(os.path.dirname(secret_file) or ".", exist_ok=True)
    try:
        with open(secret_file) as f:
            val = f.read().strip()
        if val:
            _jwt_secret = val
            return val
    except OSError:
        pass

    val = secrets.token_hex(32)
    try:
        with open(secret_file, "w") as f:
            f.write(val)
    except OSError:
        pass
    _jwt_secret = val
    return val
