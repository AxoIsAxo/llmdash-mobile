"""Extrovert OAuth 2.0 / OpenID Connect client.

Lets users log in to LLMDash with their Extrovert account (authorization
code + PKCE S256, confidential client with client_secret_post, RS256 ID
token verified against the provider's JWKS). New Extrovert identities either
log into an existing LLMDash account (linked by ``oauth_sub``), convert an
existing password account with a matching username (when auto-link is on and
the account is not already claimed), or create a fresh account.

Configuration (env): EXTROVERT_CLIENT_ID, EXTROVERT_CLIENT_SECRET,
EXTROVERT_ISSUER (default https://extrovert.redforged.eu),
EXTROVERT_AUTO_LINK (default true), EXTROVERT_ALLOW_SIGNUP (default true).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Any, Optional

import httpx
import jwt

from . import config as app_config

logger = logging.getLogger(__name__)

# --- discovery / jwks cache (1h, keyed per issuer) ---------------------------
_discovery: dict = {}
_discovery_ts: dict = {}
_jwks: dict = {}
_jwks_ts: dict = {}

STATE_TTL = 600  # seconds a /start session stays valid


def _cache_get(cache_name: str, key: str) -> Optional[dict]:
    store = _discovery if cache_name == "discovery" else _jwks
    ts_store = _discovery_ts if cache_name == "discovery" else _jwks_ts
    if key in store and time.monotonic() - ts_store.get(key, 0) < 3600:
        return store[key]
    return None


def _cache_set(cache_name: str, key: str, value: dict) -> None:
    store = _discovery if cache_name == "discovery" else _jwks
    ts_store = _discovery_ts if cache_name == "discovery" else _jwks_ts
    store[key] = value
    ts_store[key] = time.monotonic()


def _enabled() -> bool:
    return bool((app_config.settings.extrovert_client_id or "").strip())


async def _discovery_doc() -> dict:
    issuer = (app_config.settings.extrovert_issuer or "https://extrovert.redforged.eu").rstrip("/")
    cached = _cache_get("discovery", issuer)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{issuer}/.well-known/openid-configuration")
        resp.raise_for_status()
        doc = resp.json()
    _cache_set("discovery", issuer, doc)
    return doc


async def _jwks_doc() -> dict:
    doc = await _discovery_doc()
    cached = _cache_get("jwks", doc["jwks_uri"])
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(doc["jwks_uri"])
        resp.raise_for_status()
        jwks = resp.json()
    _cache_set("jwks", doc["jwks_uri"], jwks)
    return jwks


# --- PKCE ------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_verifier() -> str:
    return _b64url(secrets.token_bytes(48))


def s256_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


# --- OIDC flow -------------------------------------------------------------
async def build_authorize_url(redirect_uri: str) -> tuple[str, dict]:
    """Returns (authorize_url, session) where session holds the PKCE verifier,
    nonce and state that must be persisted until the callback."""
    doc = await _discovery_doc()
    verifier = new_verifier()
    state = _b64url(secrets.token_bytes(24))
    nonce = _b64url(secrets.token_bytes(24))
    params = {
        "client_id": app_config.settings.extrovert_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": s256_challenge(verifier),
        "code_challenge_method": "S256",
    }
    query = "&".join(f"{k}={_quote(v)}" for k, v in params.items())
    return f"{doc['authorization_endpoint']}?{query}", {
        "state": state,
        "verifier": verifier,
        "nonce": nonce,
        "redirect_uri": redirect_uri,
        "ts": time.monotonic(),
    }


async def exchange_code(code: str, session: dict) -> dict:
    """Exchange the authorization code for tokens (client_secret_post)."""
    doc = await _discovery_doc()
    body = {
        "grant_type": "authorization_code",
        "client_id": app_config.settings.extrovert_client_id,
        "client_secret": app_config.settings.extrovert_client_secret,
        "code": code,
        "code_verifier": session["verifier"],
        "redirect_uri": session["redirect_uri"],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(doc["token_endpoint"], json=body)
        resp.raise_for_status()
        return resp.json()


async def verify_id_token(id_token: str, nonce: str) -> dict:
    """Verify the RS256 ID token (iss, aud, nonce, exp) against the JWKS."""
    jwks = await _jwks_doc()
    unverified = jwt.decode(id_token, options={"verify_signature": False})
    kid = unverified.get("kid")
    key = None
    for k in jwks.get("keys", []):
        if kid is None or k.get("kid") == kid:
            key = jwt.PyJWK(k, algorithm="RS256").key
            break
    if key is None:
        raise ValueError("no matching JWKS key for id_token")
    issuer = (app_config.settings.extrovert_issuer or "https://extrovert.redforged.eu").rstrip("/")
    payload = jwt.decode(
        id_token,
        key,
        algorithms=["RS256"],
        audience=app_config.settings.extrovert_client_id,
        issuer=issuer,
        options={"require": ["exp", "iat", "iss", "aud", "nonce"]},
    )
    if payload.get("nonce") != nonce:
        raise ValueError("id_token nonce mismatch")
    return payload


async def fetch_userinfo(access_token: str) -> dict:
    doc = await _discovery_doc()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            doc.get("userinfo_endpoint", ""),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def _quote(v: str) -> str:
    from urllib.parse import quote

    return quote(v, safe="")
