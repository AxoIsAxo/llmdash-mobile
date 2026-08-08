import json
import jwt
import secrets
import time
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config as app_config
from ..database import get_db, User, UserModelUsage
from ..config_file import ConfigFileManager
from .. import extrovert_auth
from ..entitlements import get_user_entitlements
from ..models import (
    AuthSetupRequest, AuthLoginRequest, AuthRegisterRequest,
    UserResponse, UserUpdateRequest, RegistrationToggleRequest,
    ProviderConfig, ProviderConfigUpdate,
    IpLimitResponse, IpLimitUpdateRequest,
    CssUpdateRequest, AutoScrollUpdateRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# In-flight Extrovert OAuth sessions (state -> {verifier, nonce, redirect_uri, ts}).
_pending_oauth: dict[str, dict] = {}


def _oauth_redirect_uri(request: Request) -> str:
    override = (app_config.settings.extrovert_redirect_uri or "").strip()
    if override:
        return override.rstrip("/")
    return str(request.base_url).rstrip("/") + "/api/auth/extrovert/callback"


async def _unique_username(base: str, db) -> str:
    username = base or "extrovert-user"
    candidate = username
    i = 2
    while True:
        exists = (await db.execute(select(User.id).where(User.username == candidate))).scalar_one_or_none()
        if not exists:
            return candidate
        candidate = f"{username}{i}"
        i += 1


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, app_config.get_jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, app_config.get_jwt_secret(), algorithms=["HS256"])


async def _get_user_model_usage(db: AsyncSession, user_id: int) -> dict:
    result = await db.execute(
        select(UserModelUsage).where(UserModelUsage.user_id == user_id)
    )
    rows = result.scalars().all()
    return {row.model_id: {"token_usage": row.token_usage, "image_usage": row.image_usage} for row in rows}


def _user_dict(user) -> dict:
    return {
        "id": user.id, "username": user.username, "role": user.role,
        "token_usage": user.token_usage or 0, "token_limit": user.token_limit,
        "image_usage": getattr(user, "image_usage", 0) or 0, "image_limit": getattr(user, "image_limit", None),
        "extrovert_linked": bool(getattr(user, "oauth_sub", None)),
    }


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = auth[7:]
    try:
        payload = decode_token(token)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        raise HTTPException(401, "Invalid or expired token")

    result = await db.execute(select(User).where(User.id == payload["user_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")

    usage_by_model = await _get_user_model_usage(db, user.id)
    return {
        "user_id": user.id, "username": user.username, "role": user.role,
        "token_usage": user.token_usage or 0, "token_limit": user.token_limit,
        "image_usage": getattr(user, "image_usage", 0) or 0, "image_limit": getattr(user, "image_limit", None),
        "extrovert_linked": bool(getattr(user, "oauth_sub", None)),
        "token_usage_by_model": usage_by_model,
        # P9: merged entitlement flags of the user's effective plan.
        "entitlements": await get_user_entitlements(db, user.id),
    }


def require_role(*roles: str):
    async def checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return current_user
    return checker


def require_entitlement(feature: str):
    """Dependency that 403s when the user's effective plan lacks `feature`.

    Reads the entitlements dict already attached to current_user by
    get_current_user, so no extra DB round-trip per request."""
    async def checker(current_user: dict = Depends(get_current_user)):
        ents = current_user.get("entitlements") or {}
        if not ents.get(feature, True):
            raise HTTPException(
                403,
                f"Your current plan does not include: {feature.replace('_', ' ')}. "
                "Upgrade or contact an admin.",
            )
        return current_user
    return checker


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {
        "needs_setup": len(users) == 0,
        "registration_enabled": app_config.settings.registration_enabled,
        "extrovert_enabled": extrovert_auth._enabled(),
    }


async def _optional_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Like get_current_user but returns None instead of 401 (for /start?mode=link)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = decode_token(auth[7:])
    except Exception:
        return None
    return (await db.execute(select(User).where(User.id == payload.get("user_id")))).scalar_one_or_none()


async def _extrovert_username(base: str, db) -> str:
    """Namespaced username for Extrovert-auth accounts: '@' + preferred_username.
    The '@' prefix keeps Extrovert usernames distinct from normal password
    accounts, so 'userA' (normal) and 'userA' (Extrovert -> '@userA') can both
    exist; collisions get a numeric suffix."""
    base = "".join(ch for ch in (base or "").strip() if ch.isalnum() or ch in "._-")[:60] or "extrovert-user"
    return await _unique_username(f"@{base}", db)


@router.get("/extrovert/start")
async def extrovert_start(request: Request, mode: str = "login", user: User | None = Depends(_optional_user)):
    """Begin Extrovert OIDC login — returns the authorize URL (PKCE S256).

    mode=login  : the callback logs in an existing linked account or creates
                  a new @-namespaced account (no username matching).
    mode=link   : requires an authenticated user; the callback links THEIR
                  account to the Extrovert identity (renaming it to the
                  Extrovert username) — the explicit account-conversion path.
    """
    if not extrovert_auth._enabled():
        raise HTTPException(404, "Extrovert login is not configured")
    if mode == "link" and user is None:
        raise HTTPException(401, "Log in to link your Extrovert account")
    try:
        url, session = await extrovert_auth.build_authorize_url(_oauth_redirect_uri(request))
    except Exception as e:
        raise HTTPException(502, f"Could not reach Extrovert: {e}")
    if mode == "link":
        session["link_user_id"] = user.id
    _pending_oauth[session["state"]] = session
    return {"url": url}


@router.get("/extrovert/callback")
async def extrovert_callback(state: str, code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """OIDC callback — verifies the ID token, then logs in / links / signs up.

    Identity resolution is ONLY by oauth_sub (never by username):
    1. an account already linked to this Extrovert identity  -> log in;
    2. mode=link was used (authenticated user)               -> link THAT
       account and rename it to the @-namespaced Extrovert username;
    3. otherwise                                             -> create a new
       @-namespaced account.
    """
    session = _pending_oauth.pop(state, None)
    if session is None:
        return HTMLResponse(_oauth_html("Login expired. Please try again."))
    if time.monotonic() - session["ts"] > extrovert_auth.STATE_TTL:
        return HTMLResponse(_oauth_html("Login expired. Please try again."))
    try:
        tokens = await extrovert_auth.exchange_code(code, session)
        id_claims = await extrovert_auth.verify_id_token(tokens["id_token"], session["nonce"])
        info = await extrovert_auth.fetch_userinfo(tokens["access_token"])
    except Exception as e:
        return HTMLResponse(_oauth_html(f"Extrovert login failed: {e}"))

    sub = str(id_claims.get("sub") or info.get("sub") or "")
    username = str(info.get("preferred_username") or id_claims.get("preferred_username") or info.get("name") or "extrovert-user")

    link_user_id = session.get("link_user_id")
    if link_user_id is not None:
        # Explicit conversion / RE-LINK: only ever touch the authenticated
        # account. Linking a new identity replaces the previous link (the old
        # Extrovert identity simply no longer maps to this account); an
        # identity already owned by a DIFFERENT account is rejected.
        target = (await db.execute(select(User).where(User.id == link_user_id))).scalar_one_or_none()
        if target is None:
            return HTMLResponse(_oauth_html("Account not found. Please log in again and retry."))
        owner = (await db.execute(select(User).where(User.oauth_sub == sub))).scalar_one_or_none()
        if owner is not None and owner.id != target.id:
            return HTMLResponse(_oauth_html("This Extrovert account is already linked to another LLMDash account."))
        target.oauth_sub = sub  # links, or replaces the previous link
        target.username = await _extrovert_username(username, db)
        await db.commit()
        user = target
    else:
        user = (await db.execute(select(User).where(User.oauth_sub == sub))).scalar_one_or_none()
        if user is None:
            if not app_config.settings.extrovert_allow_signup:
                return HTMLResponse(_oauth_html("No LLMDash account is linked to this Extrovert account, and new signups are disabled."))
            # A new account counts against the per-IP limit, same as register.
            client_ip = request.client.host if request.client else None
            ip_limit = app_config.settings.ip_account_limit
            if client_ip and ip_limit > 0:
                ip_count = len((await db.execute(select(User).where(User.ip_address == client_ip))).scalars().all())
                if ip_count >= ip_limit:
                    return HTMLResponse(_oauth_html("Account limit reached for this IP address."))
            user = User(
                username=await _extrovert_username(username, db),
                password_hash=secrets.token_hex(32),  # OAuth-only account, no usable password
                role="user",
                oauth_sub=sub,
                ip_address=client_ip,
            )
            db.add(user)
            await db.commit()

    token = create_token(user.id, user.username, user.role)
    return HTMLResponse(_oauth_html(token=token))


def _oauth_html(error: str = "", token: str = "") -> str:
    if error:
        return f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#10131f;color:#f2f0fb;display:flex;align-items:center;justify-content:center;height:100vh">
        <div style="text-align:center;max-width:420px"><h2>LLMDash · Extrovert login</h2><p style="color:#ff5d6c">{error}</p>
        <a href="/" style="color:#ff7da3">Back to LLMDash</a></div></body></html>"""
    return f"""<!DOCTYPE html><html><body style="font-family:sans-serif;background:#10131f;color:#f2f0fb;display:flex;align-items:center;justify-content:center;height:100vh">
    <div style="text-align:center"><h2>Signed in via Extrovert</h2><p>Redirecting you back to LLMDash…</p></div>
    <script>localStorage.setItem('llmdash_token', {json.dumps(token)}); location.href = '/';</script></body></html>"""


@router.post("/setup")
async def setup_owner(req: AuthSetupRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    if result.scalars().first():
        raise HTTPException(400, "Setup already completed")

    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Username already exists")

    user = User(
        username=req.username,
        password_hash=User.hash_password(req.password),
        role="owner",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_token(user.id, user.username, user.role)
    return {"token": token, "user": {**_user_dict(user), "token_usage_by_model": {}}}


@router.post("/login")
async def login(req: AuthLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not User.verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")

    if User.is_legacy_hash(user.password_hash):
        user.password_hash = User.hash_password(req.password)
        await db.commit()

    token = create_token(user.id, user.username, user.role)
    usage_by_model = await _get_user_model_usage(db, user.id)
    return {"token": token, "user": {**_user_dict(user), "token_usage_by_model": usage_by_model}}


@router.post("/register")
async def register(req: AuthRegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if not app_config.settings.registration_enabled:
        raise HTTPException(403, "Registration is disabled")

    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Username already exists")

    client_ip = request.client.host if request.client else None

    ip_limit = app_config.settings.ip_account_limit
    if client_ip and ip_limit > 0:
        ip_count_result = await db.execute(select(User).where(User.ip_address == client_ip))
        ip_count = len(ip_count_result.scalars().all())
        if ip_count >= ip_limit:
            raise HTTPException(403, "Account limit reached for this IP address")

    user = User(
        username=req.username,
        password_hash=User.hash_password(req.password),
        role="user",
        ip_address=client_ip,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_token(user.id, user.username, user.role)
    return {"token": token, "user": {**_user_dict(user), "token_usage_by_model": {}}}


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return current_user


@router.get("/users", response_model=list[UserResponse])
async def list_users(current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.id))
    users = result.scalars().all()
    all_model_usage = await db.execute(select(UserModelUsage))
    usage_rows = all_model_usage.scalars().all()
    usage_by_user: dict = {}
    for row in usage_rows:
        usage_by_user.setdefault(row.user_id, {})[row.model_id] = {"token_usage": row.token_usage, "image_usage": row.image_usage}
    return [
        UserResponse(
            id=u.id, username=u.username, role=u.role,
            token_limit=u.token_limit, token_usage=u.token_usage or 0,
            image_limit=getattr(u, "image_limit", None), image_usage=getattr(u, "image_usage", 0) or 0,
            token_usage_by_model=usage_by_user.get(u.id, {}),
            created_at=u.created_at.isoformat() if u.created_at else "",
        )
        for u in users
    ]


@router.post("/users")
async def create_user(
    req: AuthSetupRequest,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Username already exists")

    # The IP account limit is a self-registration protection and must not
    # apply to users created by an admin.
    role = "user"
    user = User(
        username=req.username,
        password_hash=User.hash_password(req.password),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {
        "id": user.id, "username": user.username, "role": user.role,
        "token_limit": user.token_limit, "token_usage": user.token_usage or 0,
        "image_limit": getattr(user, "image_limit", None), "image_usage": getattr(user, "image_usage", 0) or 0,
        "token_usage_by_model": {},
        "created_at": user.created_at.isoformat() if user.created_at else "",
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    req: UserUpdateRequest,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    if current_user["role"] == "admin" and target.role in ("owner", "admin") and target.id != current_user["user_id"]:
        raise HTTPException(403, "Admins cannot modify other admin or owner accounts")

    if current_user["role"] == "owner" and target.role == "owner" and target.id != current_user["user_id"]:
        raise HTTPException(403, "Cannot modify another owner account")

    if req.username is not None:
        existing = await db.execute(select(User).where(User.username == req.username, User.id != user_id))
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Username already exists")
        target.username = req.username

    if req.password is not None:
        target.password_hash = User.hash_password(req.password)

    if req.role is not None:
        new_role = req.role.value
        if current_user["role"] == "admin":
            if target.role == "owner":
                raise HTTPException(403, "Admins cannot change owner role")
            if new_role == "owner":
                raise HTTPException(403, "Admins cannot grant owner role")
            if target.id == current_user["user_id"] and target.role == "admin" and new_role != "admin":
                raise HTTPException(403, "Admins cannot demote themselves")
        if current_user["role"] == "owner" and target.id == current_user["user_id"] and new_role != "owner":
            raise HTTPException(403, "Cannot demote yourself from owner")

        target.role = new_role

    if req.token_limit is not None:
        target.token_limit = req.token_limit if req.token_limit > 0 else None

    if req.image_limit is not None:
        target.image_limit = req.image_limit if req.image_limit > 0 else None

    await db.commit()
    return {"status": "updated"}


@router.post("/me/delete")
async def delete_me(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Delete the authenticated user's own account (conversations cascade,
    memory store wiped). The last owner account cannot be deleted."""
    if current_user["role"] == "owner":
        owners = (await db.execute(select(User).where(User.role == "owner"))).scalars().all()
        if len(owners) <= 1:
            raise HTTPException(400, "Cannot delete the last owner account")
    target = (await db.execute(select(User).where(User.id == current_user["user_id"]))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "User not found")
    await db.delete(target)
    await db.commit()
    try:
        from ..memory.store import Store

        Store(app_config.settings.memory_dir).reset_user(current_user["user_id"])
    except Exception:
        pass
    return {"status": "deleted"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user["user_id"]:
        raise HTTPException(400, "Cannot delete yourself")

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    if current_user["role"] == "admin" and target.role in ("owner", "admin"):
        raise HTTPException(403, "Admins cannot delete admin or owner accounts")

    if current_user["role"] == "owner" and target.role == "owner":
        raise HTTPException(403, "Cannot delete another owner account")

    await db.delete(target)
    await db.commit()
    return {"status": "deleted"}


@router.get("/registration")
async def get_registration(current_user: dict = Depends(require_role("owner", "admin"))):
    return {"enabled": app_config.settings.registration_enabled}


@router.post("/registration")
async def toggle_registration(req: RegistrationToggleRequest, current_user: dict = Depends(require_role("owner", "admin"))):
    ConfigFileManager.update("data/.env", {"REGISTRATION_ENABLED": "true" if req.enabled else "false"})
    app_config.reload_settings()
    return {"enabled": app_config.settings.registration_enabled}


@router.get("/ip-limit", response_model=IpLimitResponse)
async def get_ip_limit(current_user: dict = Depends(require_role("owner", "admin"))):
    return {"limit": app_config.settings.ip_account_limit}


@router.post("/ip-limit", response_model=IpLimitResponse)
async def set_ip_limit(req: IpLimitUpdateRequest, current_user: dict = Depends(require_role("owner", "admin"))):
    ConfigFileManager.update("data/.env", {"IP_ACCOUNT_LIMIT": str(req.limit)})
    app_config.reload_settings()
    return {"limit": app_config.settings.ip_account_limit}


@router.post("/users/{user_id}/reset-usage")
async def reset_user_usage(
    user_id: int,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    if current_user["role"] == "admin" and target.role in ("owner", "admin") and target.id != current_user["user_id"]:
        raise HTTPException(403, "Admins cannot modify other admin or owner accounts")

    target.token_usage = 0
    target.image_usage = 0
    from sqlalchemy import delete
    await db.execute(delete(UserModelUsage).where(UserModelUsage.user_id == user_id))
    await db.commit()
    return {"status": "reset"}


@router.get("/css")
async def get_user_css(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    return {"css": user.custom_css or ""}


@router.put("/css")
async def save_user_css(
    req: CssUpdateRequest,
    current_user: dict = Depends(require_entitlement("theme_editing")),
    db: AsyncSession = Depends(get_db),
):
    from ..theme import validate_raw_css
    ok, err = validate_raw_css(req.css)
    if not ok:
        raise HTTPException(400, f"Invalid CSS: {err}")
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.custom_css = req.css or None
    await db.commit()
    return {"status": "saved"}


@router.get("/auto-scroll")
async def get_auto_scroll(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    value = user.auto_scroll if user and user.auto_scroll is not None else True
    return {"auto_scroll": value}


@router.put("/auto-scroll")
async def set_auto_scroll(req: AutoScrollUpdateRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.auto_scroll = req.auto_scroll
    await db.commit()
    return {"status": "saved", "auto_scroll": req.auto_scroll}


import os

PROVIDER_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "provider_configs.json")

DEFAULT_PROVIDERS = [
    {"key": "deepseek", "name": "DeepSeek", "type": "openai_compatible", "env_var": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/v1"},
    {"key": "anthropic", "name": "Anthropic", "type": "anthropic", "env_var": "ANTHROPIC_API_KEY", "base_url": "https://api.anthropic.com"},
    {"key": "minimax", "name": "MiniMax", "type": "openai_compatible", "env_var": "MINIMAX_API_KEY", "base_url": "https://api.minimax.chat/v1"},
    {"key": "openrouter", "name": "OpenRouter", "type": "openai_compatible", "env_var": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1"},
]


def load_provider_configs():
    if os.path.exists(PROVIDER_FILE):
        try:
            with open(PROVIDER_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return list(DEFAULT_PROVIDERS)


def save_provider_configs(configs):
    with open(PROVIDER_FILE, "w") as f:
        json.dump(configs, f, indent=2)


@router.get("/providers", response_model=list[ProviderConfig])
async def list_providers(current_user: dict = Depends(require_role("owner", "admin"))):
    return load_provider_configs()


@router.put("/providers/{provider_key}")
async def update_provider(
    provider_key: str,
    req: ProviderConfigUpdate,
    current_user: dict = Depends(require_role("owner", "admin")),
):
    configs = load_provider_configs()
    for cfg in configs:
        if cfg["key"] == provider_key:
            if req.name is not None:
                cfg["name"] = req.name
            if req.base_url is not None:
                cfg["base_url"] = req.base_url
            save_provider_configs(configs)
            return {"status": "updated", "provider": cfg}
    raise HTTPException(404, "Provider not found")
