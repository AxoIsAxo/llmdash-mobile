import json
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config as app_config
from ..database import get_db, User, Conversation, UserModelUsage
from ..models import (
    AuthSetupRequest, AuthLoginRequest, AuthRegisterRequest,
    UserResponse, UserUpdateRequest, RegistrationToggleRequest,
    ProviderConfig, ProviderConfigUpdate,
    IpLimitResponse, IpLimitUpdateRequest,
    CssUpdateRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    return jwt.encode(payload, app_config.settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, app_config.settings.jwt_secret, algorithms=["HS256"])


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
    }


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    elif request.query_params.get("token"):
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(401, "Not authenticated")
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
        "token_usage_by_model": usage_by_model,
    }


def require_role(*roles: str):
    async def checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(403, "Insufficient permissions")
        return current_user
    return checker


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return {
        "needs_setup": len(users) == 0,
        "registration_enabled": app_config.settings.registration_enabled,
    }


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
            raise HTTPException(403, f"Account limit reached for this IP address")

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
    return {"token": token, "user": {"id": user.id, "username": user.username, "role": user.role}}


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
    request: Request,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Username already exists")

    client_ip = request.client.host if request.client else None

    ip_limit = app_config.settings.ip_account_limit
    if client_ip and ip_limit > 0:
        ip_count_result = await db.execute(select(User).where(User.ip_address == client_ip))
        ip_count = len(ip_count_result.scalars().all())
        if ip_count >= ip_limit:
            raise HTTPException(403, f"Account limit reached for this IP address")

    role = "user"
    user = User(
        username=req.username,
        password_hash=User.hash_password(req.password),
        role=role,
        ip_address=client_ip,
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
    env_path = ".env"
    existing = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

    existing["REGISTRATION_ENABLED"] = "true" if req.enabled else "false"

    lines = []
    for k, v in existing.items():
        if v:
            if " " in v or "#" in v:
                lines.append(f'{k}="{v}"')
            else:
                lines.append(f"{k}={v}")
        else:
            lines.append(f"{k}=")
    lines.append("")

    with open(env_path, "w") as f:
        f.write("\n".join(lines))

    app_config.reload_settings()
    return {"enabled": app_config.settings.registration_enabled}


@router.get("/ip-limit", response_model=IpLimitResponse)
async def get_ip_limit(current_user: dict = Depends(require_role("owner", "admin"))):
    return {"limit": app_config.settings.ip_account_limit}


@router.post("/ip-limit", response_model=IpLimitResponse)
async def set_ip_limit(req: IpLimitUpdateRequest, current_user: dict = Depends(require_role("owner", "admin"))):
    env_path = ".env"
    existing = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

    existing["IP_ACCOUNT_LIMIT"] = str(req.limit)

    lines = []
    for k, v in existing.items():
        if v:
            if " " in v or "#" in v:
                lines.append(f'{k}="{v}"')
            else:
                lines.append(f"{k}={v}")
        else:
            lines.append(f"{k}=")
    lines.append("")

    with open(env_path, "w") as f:
        f.write("\n".join(lines))

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
async def save_user_css(req: CssUpdateRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.custom_css = req.css or None
    await db.commit()
    return {"status": "saved"}


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
