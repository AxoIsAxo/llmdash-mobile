import secrets
from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    database_path: str = "data/llmdash.db"
    jwt_secret: str = secrets.token_hex(32)
    registration_enabled: bool = False
    ip_account_limit: int = 3
    deepseek_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    minimax_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    searxng_url: str = "http://localhost:8080"
    lnbits_url: Optional[str] = None
    lnbits_invoice_key: Optional[str] = None

    model_config = {"env_file": "data/.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()


ENV_VAR_MAP = {
    "DEEPSEEK_API_KEY": "deepseek_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "MINIMAX_API_KEY": "minimax_api_key",
    "OPENROUTER_API_KEY": "openrouter_api_key",
    "LNBITS_URL": "lnbits_url",
    "LNBITS_INVOICE_KEY": "lnbits_invoice_key",
}


def reload_settings():
    global settings
    settings = Settings()
