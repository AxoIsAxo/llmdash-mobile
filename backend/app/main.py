import asyncio
import base64
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, APIRouter, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from . import config as app_config
from .database import init_db, get_db, async_session, ModelConfig, Conversation, Message, User, TokenUsageLog, UserModelUsage, SubscriptionPlan, PlanModelLimit, UserSubscription
from .models import (
    ModelConfigCreate, ModelConfigUpdate, ModelConfigResponse,
    ConversationCreate, ConversationResponse,
    ChatRequest, MessageResponse, BranchRequest,
    EnvUpdateRequest, EnvStatusResponse, GenerateStatusResponse,
    ModelReorderRequest,
    ImageGenerationRequest, ImageGenerationResponse,
    UploadResponse, FileUploadSettings, FileUploadSettingsUpdate,
)
from .ai import get_provider, ToolDef
from .tools import get_tool_definitions, execute_tool
from .sandbox import is_docker_available
from .ocr import process_uploaded_file, is_allowed_file, is_image_file, ocr_image, IMAGE_EXTENSIONS, is_ocr_available
from .whisper_stt import transcribe_audio
from .routers.auth import router as auth_router, get_current_user, require_role, load_provider_configs
from .routers.subscriptions import router as subscriptions_router

router = APIRouter(prefix="/api")

active_generations: dict[int, dict] = {}
"""Per-conversation active generation state.
Schema: {conv_id: {"queues": set[asyncio.Queue], "task": asyncio.Task, "message_id": int}}"""


def _push_to_queues(conv_id: int, event: str):
    gen = active_generations.get(conv_id)
    if gen:
        for q in list(gen.get("queues", [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


def _cleanup_generation(conv_id: int):
    gen = active_generations.pop(conv_id, None)
    if gen:
        for q in gen.get("queues", []):
            try:
                q.put_nowait("data: [DONE]\n\n")
            except Exception:
                pass


def get_active_provider_configs():
    return load_provider_configs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with async_session() as sess:
        result = await sess.execute(
            select(Message).where(Message.status == "generating")
        )
        stuck = result.scalars().all()
        for msg in stuck:
            msg.status = "interrupted"
        if stuck:
            await sess.commit()
    yield


app = FastAPI(title="LLMDash", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def coi_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
    return response

app.include_router(auth_router)
app.include_router(subscriptions_router)


# --- Model Config (admin only) ---

@router.get("/models", response_model=list[ModelConfigResponse])
async def list_models(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(ModelConfig).order_by(ModelConfig.sort_order.is_(None), ModelConfig.sort_order, ModelConfig.id)
    if current_user.get("role") not in ("owner", "admin"):
        query = query.where(ModelConfig.enabled == True)
    result = await db.execute(query)
    models = result.scalars().all()
    return [
        ModelConfigResponse(
            id=m.id, name=m.name, provider=m.provider,
            model_name=m.model_name, model_type=getattr(m, "model_type", "chat") or "chat",
            base_url=m.base_url,
            api_key_env=m.api_key_env, temperature=m.temperature or 0.7,
            max_tokens=m.max_tokens or 4096,
            thinking_enabled=bool(getattr(m, "thinking_enabled", False)),
            thinking_budget_tokens=getattr(m, "thinking_budget_tokens", None),
            vision_enabled=bool(getattr(m, "vision_enabled", False)),
            tools_enabled=bool(getattr(m, "tools_enabled", True)),
            enabled=m.enabled,
            sort_order=getattr(m, "sort_order", None),
            created_at=m.created_at.isoformat() if m.created_at else "",
            updated_at=m.updated_at.isoformat() if m.updated_at else "",
        )
        for m in models
    ]


@router.post("/models", status_code=201)
async def create_model(cfg: ModelConfigCreate, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    if cfg.sort_order is None:
        max_result = await db.execute(select(ModelConfig).order_by(ModelConfig.sort_order.desc()).limit(1))
        max_model = max_result.scalar_one_or_none()
        max_sort = (max_model.sort_order or 0) if max_model else 0
        sort_order = max_sort + 1
    else:
        sort_order = cfg.sort_order
    model_type_val = getattr(cfg, "model_type", "chat")
    if hasattr(model_type_val, "value"):
        model_type_val = model_type_val.value
    model = ModelConfig(
        name=cfg.name, provider=cfg.provider.value,
        model_name=cfg.model_name, model_type=model_type_val or "chat",
        base_url=cfg.base_url,
        api_key_env=cfg.api_key_env, temperature=cfg.temperature,
        max_tokens=cfg.max_tokens,
        thinking_enabled=getattr(cfg, "thinking_enabled", False),
        thinking_budget_tokens=getattr(cfg, "thinking_budget_tokens", None),
        vision_enabled=getattr(cfg, "vision_enabled", False),
        tools_enabled=getattr(cfg, "tools_enabled", True),
        enabled=cfg.enabled,
        sort_order=sort_order,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return {"id": model.id, "status": "created"}


@router.put("/models/reorder")
async def reorder_models(req: ModelReorderRequest, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    for i, model_id in enumerate(req.model_ids):
        await db.execute(
            update(ModelConfig).where(ModelConfig.id == model_id).values(sort_order=i)
        )
    await db.commit()
    return {"status": "reordered"}


@router.put("/models/{model_id}")
async def update_model(model_id: int, cfg: ModelConfigUpdate, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    update_data = cfg.model_dump(exclude_unset=True)
    if "provider" in update_data and update_data["provider"] is not None:
        update_data["provider"] = update_data["provider"].value
    if "model_type" in update_data and update_data["model_type"] is not None:
        if hasattr(update_data["model_type"], "value"):
            update_data["model_type"] = update_data["model_type"].value
    for key, value in update_data.items():
        setattr(model, key, value)
    model.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated"}


IMAGE_MODEL_PATTERNS = [
    "dall-e", "dalle", "imagen", "flux", "stable-diffusion", "sdxl", "sd3",
    "midjourney", "recraft", "playground", "runwayml", "proteus", "dreamshaper",
    "animagine", "anything-v", "sweetai", "pg-", "rvr", "openjourney",
    "epicrealism", "juggernaut", "artifusion", "luna-diffusion", "pixart",
    "kolors", "hunyuan-3d", "janus", "seedream", "wai-", "noobai", "crystal-clear",
    "realistic-vision", "meinamix", "majicmix", "ghostmix", "babes", "perfectly-",
    "counterfeit", "aingdiffusion", "deliberate", "disney-", "dreamlike",
    "pfg-art", "citrine-dream", "photonic", "realism-engine", "aniverse",
    "flat-", "samaritan", "pixar-", "retro-", "hasdx",
    "image", "img-gen", "txt2img", "img2img",
    "sdxl", "sd-", "turbo", "lightning", "hyper",
]
IMAGE_MODEL_LOWERS = [p.lower() for p in IMAGE_MODEL_PATTERNS]

VISION_MODEL_PATTERNS = [
    "gpt-4o", "gpt-4-turbo", "gpt-4-vision", "vision",
    "claude-3", "claude-3.5", "claude-3.7",
    "gemini-2", "gemini-1.5", "gemini-pro-vision",
    "pixtral", "llava", "bakllava", "cogvlm", "fuyu",
    "qwen-vl", "qwen2-vl", "qwen2.5-vl",
    "deepseek-vl", "deepseek-vl2",
    "minimax-vl", "minimax-vision",
    "glm-4v", "cogview",
    "internvl", "internlm-xcomposer",
    "phi-3-vision", "phi-3.5-vision",
    "llama-3.2-vision", "llama-vision",
    "molmo", "idefics", "paligemma",
    "yi-vision", "yi-vl",
]
VISION_MODEL_LOWERS = [p.lower() for p in VISION_MODEL_PATTERNS]


def _detect_model_type(model_id: str, raw_entry: dict | None = None) -> str:
    if raw_entry:
        arch = raw_entry.get("architecture", {}) or {}
        out_mods = arch.get("output_modalities", []) or []
        if out_mods and "image" in out_mods:
            modality = arch.get("modality", "")
            if "->image" in modality:
                return "image"
    lower = model_id.lower()
    if "/" in lower:
        lower = lower.split("/", 1)[1]
    for pat in IMAGE_MODEL_LOWERS:
        if pat in lower:
            return "image"
    return "chat"


def _detect_vision_capability(model_id: str, raw_entry: dict | None = None) -> bool:
    if raw_entry:
        arch = raw_entry.get("architecture", {}) or {}
        in_mods = arch.get("input_modalities", []) or arch.get("modalities", []) or []
        if "image" in in_mods or "image_url" in in_mods:
            return True
    lower = model_id.lower()
    if "/" in lower:
        lower = lower.split("/", 1)[1]
    for pat in VISION_MODEL_LOWERS:
        if pat in lower:
            return True
    return False


@router.get("/models/scan")
async def scan_models(current_user: dict = Depends(require_role("owner", "admin"))):
    provider_configs = get_active_provider_configs()
    results = []
    for pc in provider_configs:
        api_key = getattr(app_config.settings, pc["env_var"].lower(), None)
        if not api_key:
            continue
        models = []
        error = None
        try:
            if pc["type"] == "openai_compatible":
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        f"{pc['base_url']}/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = data.get("data", data.get("models", []))
                        for m in raw:
                            mid = m.get("id", m.get("name", ""))
                            models.append({
                                "id": mid,
                                "name": m.get("id", m.get("name", "")),
                                "suggested_type": _detect_model_type(mid, m),
                                "supports_vision": _detect_vision_capability(mid, m),
                            })
                    else:
                        error = f"HTTP {resp.status_code}"
                    try:
                        img_resp = await client.get(
                            f"{pc['base_url']}/models",
                            headers={"Authorization": f"Bearer {api_key}"},
                            params={"output_modalities": "image"},
                        )
                        if img_resp.status_code == 200:
                            img_data = img_resp.json()
                            img_raw = img_data.get("data", img_data.get("models", []))
                            seen_ids = {m["id"] for m in models}
                            for m in img_raw:
                                mid = m.get("id", m.get("name", ""))
                                if mid not in seen_ids:
                                    models.append({
                                        "id": mid,
                                        "name": m.get("id", m.get("name", "")),
                                        "suggested_type": "image",
                                        "supports_vision": _detect_vision_capability(mid, m),
                                    })
                    except Exception:
                        pass
            elif pc["type"] == "anthropic":
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        for m in data.get("data", []):
                            mid = m.get("id", m.get("name", ""))
                            models.append({
                                "id": mid,
                                "name": m.get("display_name", m.get("id", "")),
                                "suggested_type": _detect_model_type(mid),
                                "supports_vision": _detect_vision_capability(mid),
                            })
                    else:
                        error = f"HTTP {resp.status_code}"
        except Exception as e:
            error = str(e)

        results.append({
            "provider_key": pc["key"],
            "provider_name": pc["name"],
            "provider_type": pc["type"],
            "base_url": pc["base_url"],
            "env_var": pc["env_var"],
            "models": models,
            "error": error,
        })
    return results


@router.delete("/models/{model_id}")
async def delete_model(model_id: int, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    await db.delete(model)
    await db.commit()
    return {"status": "deleted"}


@router.get("/models/{model_id}")
async def get_model(model_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    return ModelConfigResponse(
        id=model.id, name=model.name, provider=model.provider,
        model_name=model.model_name, model_type=getattr(model, "model_type", "chat") or "chat",
        base_url=model.base_url,
        api_key_env=model.api_key_env, temperature=model.temperature or 0.7,
        max_tokens=model.max_tokens or 4096,
        thinking_enabled=bool(getattr(model, "thinking_enabled", False)),
        thinking_budget_tokens=getattr(model, "thinking_budget_tokens", None),
        vision_enabled=bool(getattr(model, "vision_enabled", False)),
        tools_enabled=bool(getattr(model, "tools_enabled", True)),
        enabled=model.enabled,
        sort_order=getattr(model, "sort_order", None),
        created_at=model.created_at.isoformat() if model.created_at else "",
        updated_at=model.updated_at.isoformat() if model.updated_at else "",
    )


# --- Conversations (scoped to user) ---

@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == current_user["user_id"]).order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()
    return [
        ConversationResponse(
            id=c.id, title=c.title, model_id=c.model_id, user_id=c.user_id,
            created_at=c.created_at.isoformat() if c.created_at else "",
            updated_at=c.updated_at.isoformat() if c.updated_at else "",
        )
        for c in convs
    ]


@router.post("/conversations", status_code=201)
async def create_conversation(cfg: ConversationCreate, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv = Conversation(title=cfg.title or "New Chat", model_id=cfg.model_id, user_id=current_user["user_id"])
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationResponse(
        id=conv.id, title=conv.title, model_id=conv.model_id, user_id=conv.user_id,
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else "",
    )


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    await db.execute(delete(Message).where(Message.conversation_id == conv_id))
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted"}


@router.get("/conversations/{conv_id}/messages", response_model=list[MessageResponse])
async def get_messages(conv_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(404, "Conversation not found")

    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
    )
    msgs = result.scalars().all()
    return [
        MessageResponse(
            id=m.id, role=m.role, content=m.content,
            attachments_json=json.loads(m.attachments_json) if m.attachments_json else None,
            tool_calls_json=json.loads(m.tool_calls_json) if m.tool_calls_json else None,
            tool_call_id=m.tool_call_id, tool_name=m.tool_name,
            reasoning_content=m.reasoning_content,
            status=m.status or "done",
            created_at=m.created_at.isoformat() if m.created_at else "",
        )
        for m in msgs
    ]


@router.delete("/conversations/{conv_id}/messages")
async def clear_messages(conv_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(404, "Conversation not found")
    await db.execute(delete(Message).where(Message.conversation_id == conv_id))
    await db.commit()
    return {"status": "cleared"}


@router.post("/conversations/{conv_id}/branch")
async def branch_conversation(conv_id: int, req: BranchRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")

    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()
    limit = req.message_index
    if limit < 0 or limit >= len(messages):
        raise HTTPException(400, f"message_index {limit} out of range (0-{len(messages)-1})")

    branch = Conversation(title=f"{conv.title} (branch)", model_id=conv.model_id, user_id=current_user["user_id"])
    db.add(branch)
    await db.flush()

    for i in range(limit + 1):
        m = messages[i]
        copy = Message(
            conversation_id=branch.id,
            role=m.role,
            content=m.content,
            attachments_json=m.attachments_json,
            tool_calls_json=m.tool_calls_json,
            tool_call_id=m.tool_call_id,
            tool_name=m.tool_name,
            reasoning_content=m.reasoning_content,
        )
        db.add(copy)

    await db.commit()
    await db.refresh(branch)

    return ConversationResponse(
        id=branch.id, title=branch.title, model_id=branch.model_id, user_id=branch.user_id,
        created_at=branch.created_at.isoformat() if branch.created_at else "",
        updated_at=branch.updated_at.isoformat() if branch.updated_at else "",
    )


# --- Env Config (admin only) ---

@router.get("/config/env", response_model=EnvStatusResponse)
async def list_env_vars(current_user: dict = Depends(require_role("owner", "admin"))):
    configured = [k for k, v in app_config.ENV_VAR_MAP.items() if getattr(app_config.settings, v, None)]
    available = list(app_config.ENV_VAR_MAP.keys())
    return EnvStatusResponse(configured=configured, available=available)


@router.post("/config/env")
async def update_env_vars(req: EnvUpdateRequest, current_user: dict = Depends(require_role("owner", "admin"))):
    env_path = "data/.env"
    os.makedirs("data", exist_ok=True)
    existing = {}
    if os.path.exists(env_path) and not os.path.isdir(env_path):
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        existing[k.strip()] = v.strip()
        except (OSError, IOError):
            existing = {}

    for key, value in req.updates.items():
        if key in app_config.ENV_VAR_MAP or key in existing:
            existing[key] = value
        else:
            existing[key] = value

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
    return {"status": "updated", "updated_keys": list(req.updates.keys())}


@router.get("/config/status")
async def config_status(current_user: dict = Depends(get_current_user)):
    docker_ok = is_docker_available()
    return {
        "docker_available": docker_ok,
        "tools": {
            "web_search": True,
            "document_editor": True,
            "render_html": True,
            "sandbox": docker_ok,
        },
        "providers": {
            "deepseek": bool(app_config.settings.deepseek_api_key),
            "anthropic": bool(app_config.settings.anthropic_api_key),
            "minimax": bool(app_config.settings.minimax_api_key),
            "openrouter": bool(app_config.settings.openrouter_api_key),
        },
        "ocr_available": is_ocr_available(),
        "uploads": {
            "enabled": app_config.settings.file_upload_enabled,
            "ocr_enabled": app_config.settings.ocr_enabled,
            "ocr_strategy": app_config.settings.ocr_strategy,
        },
    }


@router.get("/config/uploads", response_model=FileUploadSettings)
async def get_upload_settings(current_user: dict = Depends(require_role("owner", "admin"))):
    return FileUploadSettings(
        file_upload_enabled=app_config.settings.file_upload_enabled,
        ocr_enabled=app_config.settings.ocr_enabled,
        ocr_strategy=app_config.settings.ocr_strategy,
        whisper_model=app_config.settings.whisper_model,
        whisper_compute_type=app_config.settings.whisper_compute_type,
        whisper_device=app_config.settings.whisper_device,
        whisper_language=app_config.settings.whisper_language,
        whisper_beam_size=app_config.settings.whisper_beam_size,
    )


@router.put("/config/uploads", response_model=FileUploadSettings)
async def update_upload_settings(req: FileUploadSettingsUpdate, current_user: dict = Depends(require_role("owner", "admin"))):
    updates = {}
    if req.file_upload_enabled is not None:
        updates["FILE_UPLOAD_ENABLED"] = str(req.file_upload_enabled).lower()
    if req.ocr_enabled is not None:
        updates["OCR_ENABLED"] = str(req.ocr_enabled).lower()
    if req.ocr_strategy is not None:
        updates["OCR_STRATEGY"] = req.ocr_strategy
    if req.whisper_model is not None:
        updates["WHISPER_MODEL"] = req.whisper_model
    if req.whisper_compute_type is not None:
        updates["WHISPER_COMPUTE_TYPE"] = req.whisper_compute_type
    if req.whisper_device is not None:
        updates["WHISPER_DEVICE"] = req.whisper_device
    if req.whisper_language is not None:
        updates["WHISPER_LANGUAGE"] = req.whisper_language
    if req.whisper_beam_size is not None:
        updates["WHISPER_BEAM_SIZE"] = str(int(req.whisper_beam_size))

    if updates:
        env_path = "data/.env"
        os.makedirs("data", exist_ok=True)
        existing = {}
        if os.path.exists(env_path) and not os.path.isdir(env_path):
            try:
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            existing[k.strip()] = v.strip()
            except (OSError, IOError):
                existing = {}
        for key, value in updates.items():
            existing[key] = value
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

    return FileUploadSettings(
        file_upload_enabled=app_config.settings.file_upload_enabled,
        ocr_enabled=app_config.settings.ocr_enabled,
        ocr_strategy=app_config.settings.ocr_strategy,
        whisper_model=app_config.settings.whisper_model,
        whisper_compute_type=app_config.settings.whisper_compute_type,
        whisper_device=app_config.settings.whisper_device,
        whisper_language=app_config.settings.whisper_language,
        whisper_beam_size=app_config.settings.whisper_beam_size,
    )


@router.get("/config/whisper")
async def get_whisper_config(current_user: dict = Depends(get_current_user)):
    from .whisper_stt import VALID_MODEL_SIZES, VALID_COMPUTE_TYPES, DEFAULT_MODEL_SIZE
    model = app_config.settings.whisper_model
    if model not in VALID_MODEL_SIZES:
        model = DEFAULT_MODEL_SIZE
    compute_type = app_config.settings.whisper_compute_type
    if compute_type not in VALID_COMPUTE_TYPES:
        compute_type = "int8"
    beam = app_config.settings.whisper_beam_size or 1
    beam = max(1, min(int(beam), 10))
    return {
        "model": model,
        "compute_type": compute_type,
        "device": app_config.settings.whisper_device or "auto",
        "language": app_config.settings.whisper_language,
        "beam_size": beam,
        "available_models": list(VALID_MODEL_SIZES),
        "available_compute_types": list(VALID_COMPUTE_TYPES),
    }


@router.post("/chat/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not app_config.settings.file_upload_enabled:
        raise HTTPException(403, "File uploads are disabled by the admin")

    if not file.filename:
        raise HTTPException(400, "No filename provided")

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file.filename)
    if not is_allowed_file(safe_name):
        raise HTTPException(400, "File type not allowed")

    uploads_dir = app_config.settings.uploads_dir
    os.makedirs(uploads_dir, exist_ok=True)

    file_id = uuid.uuid4().hex[:12]
    stored_name = f"{file_id}_{safe_name}"
    file_path = os.path.join(uploads_dir, stored_name)

    content = await file.read()
    max_size = 20 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(400, f"File too large (max {max_size // (1024*1024)}MB)")

    with open(file_path, "wb") as f:
        f.write(content)

    force_ocr = not app_config.settings.ocr_enabled
    result = process_uploaded_file(file_path, file.filename, force_ocr=False)

    return UploadResponse(
        filename=stored_name,
        file_path=f"/api/uploads/{stored_name}",
        file_type=result["file_type"],
        file_size=result["file_size"],
        ocr_text=result.get("ocr_text"),
    )


@router.post("/chat/transcribe")
async def transcribe_voice(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if not file.filename:
        raise HTTPException(400, "No audio file provided")
    content = await file.read()
    max_size = 10 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(413, "Audio too large (max 10MB)")
    try:
        text = transcribe_audio(content)
        return {"text": text}
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {str(e)}")


@router.get("/uploads/{filename}")
async def serve_upload(filename: str, current_user: dict = Depends(get_current_user)):
    safe_name = os.path.basename(filename)
    uploads_dir = os.path.realpath(app_config.settings.uploads_dir)
    filepath = os.path.join(uploads_dir, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(404, "File not found")
    real = os.path.realpath(filepath)
    if not real.startswith(uploads_dir):
        raise HTTPException(404, "File not found")
    return FileResponse(filepath, filename=safe_name)


# --- Chat ---

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == req.conversation_id, Conversation.user_id == current_user["user_id"])
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(404, "Conversation not found")

    model_id = req.model_id or conversation.model_id
    if not model_id:
        raise HTTPException(400, "No model configured for this conversation")

    model_result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = model_result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    if not model.enabled:
        raise HTTPException(400, "Model is disabled")

    model_type = getattr(model, "model_type", "chat") or "chat"
    if model_type != "chat":
        raise HTTPException(400, "This model is not a chat model. Use /api/chat/image for image generation models.")

    provider = get_provider(model.provider)
    if not provider:
        raise HTTPException(400, f"Unknown provider: {model.provider}")

    if model.api_key_env:
        env_attr = model.api_key_env.lower()
        key_val = getattr(app_config.settings, env_attr, None)
        if not key_val:
            raise HTTPException(400, f"API key not configured for {model.name}. Set the {model.api_key_env} environment variable in Settings.")

    user_result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = user_result.scalar_one_or_none()

    subscription_limit = None
    model_subscription_limit = None
    subscription_result = await db.execute(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            UserSubscription.user_id == current_user["user_id"],
            UserSubscription.status == "active",
        )
        .where(
            (UserSubscription.expires_at > datetime.now(timezone.utc))
            | (UserSubscription.expires_at == None)
        )
    )
    active_sub_row = subscription_result.first()
    free_plan = None
    free_plan_result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "Free")
    )
    free_plan = free_plan_result.scalar_one_or_none()

    if active_sub_row:
        sub, plan = active_sub_row
        if plan.token_limit is not None:
            subscription_limit = plan.token_limit
        model_limit_result = await db.execute(
            select(PlanModelLimit).where(
                PlanModelLimit.plan_id == plan.id,
                PlanModelLimit.model_id == model_id,
            )
        )
        model_limit = model_limit_result.scalar_one_or_none()
        if model_limit and model_limit.token_limit is not None:
            model_subscription_limit = model_limit.token_limit

    if not active_sub_row:
        if free_plan and free_plan.token_limit is not None:
            subscription_limit = free_plan.token_limit

    if not active_sub_row and free_plan and model_subscription_limit is None:
        model_limit_result = await db.execute(
            select(PlanModelLimit).where(
                PlanModelLimit.plan_id == free_plan.id,
                PlanModelLimit.model_id == model_id,
            )
        )
        model_limit = model_limit_result.scalar_one_or_none()
        if model_limit and model_limit.token_limit is not None:
            model_subscription_limit = model_limit.token_limit

    global_limit = None
    if user and user.token_limit is not None:
        global_limit = user.token_limit
    if subscription_limit is not None:
        global_limit = subscription_limit if global_limit is None else min(global_limit, subscription_limit)

    if user and model_subscription_limit is not None:
        per_model_result = await db.execute(
            select(UserModelUsage).where(
                UserModelUsage.user_id == current_user["user_id"],
                UserModelUsage.model_id == model_id,
            )
        )
        per_model_row = per_model_result.scalar_one_or_none()
        per_model_usage = per_model_row.token_usage if per_model_row else 0
        if per_model_usage >= model_subscription_limit:
            raise HTTPException(403, f"Token limit reached for this model ({per_model_usage}/{model_subscription_limit}). Upgrade your plan or contact an admin.")

    if user and global_limit is not None and (user.token_usage or 0) >= global_limit:
        raise HTTPException(403, f"Token limit reached ({user.token_usage}/{global_limit}). Upgrade your plan or contact an admin.")

    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == req.conversation_id).order_by(Message.created_at)
    )
    db_messages = msg_result.scalars().all()

    messages = []
    for m in db_messages:
        entry = {"role": m.role, "content": m.content or ""}
        if m.tool_calls_json:
            entry["tool_calls_json"] = m.tool_calls_json
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.tool_name:
            entry["tool_name"] = m.tool_name
        if m.reasoning_content:
            entry["reasoning_content"] = m.reasoning_content
        messages.append(entry)

    now = datetime.now(timezone.utc)
    system_prompt = (
        f"You are {model.name}, a helpful AI assistant running on LLMDash.\n"
        f"The current UTC date and time is {now.strftime('%Y-%m-%d %H:%M:%S')} UTC "
        f"({now.strftime('%A, %B %d, %Y')}).\n"
        "You have access to built-in tools including web_search which queries SearXNG for real-time "
        "information from the internet. Use web_search when the user asks about current events, "
        "recent news, live data, or any topic where your training data may be outdated.\n"
        "When searching the web, use specific and concise queries. Cite sources when providing "
        "information obtained from web searches.\n"
        "You can also create and edit documents, render HTML, and execute code in a sandboxed "
        "environment. Be thorough, accurate, and helpful.\n"
        "IMPORTANT: Always invoke tools through the platform's native tool-calling "
        "interface (the tools you were given). Never output raw `<tool_call>...</tool_call>` "
        "XML/JSON in your visible reply — the chat renderer does not interpret those "
        "tags and they will appear as broken text to the user.\n"
        "\n"
        "The user can ask you to restyle the entire LLMDash UI. The CSS tools are NOT limited to "
        "colors — the user stylesheet controls colors, backgrounds, borders, border-radius, shadows, "
        "spacing, font family, font size, font weight, line-height, opacity, transitions, animations, "
        "layout widths, and z-index. When the user asks to \"change the style\" / \"restyle\" / "
        "\"make it look like X\" / \"change the font\" / \"round the corners\" / \"make the chat "
        "wider\" / \"add shadows\" / etc., use the CSS tools to do it. The stylesheet is a normal "
        "CSS string; you can override Tailwind utility classes, change :root custom properties, or "
        "add new rules for any selector.\n"
        "\n"
        "When editing the user's CSS:\n"
        "1. Always call get_user_css first to read the current state.\n"
        "2. Use patch_user_css for targeted changes — provide enough surrounding lines in old_str to make it unique.\n"
        "3. Use append_user_css to add new rules.\n"
        "4. Never use set_user_css unless asked to fully reset or rewrite all styles.\n"
        "5. If patch_user_css returns an error, call get_user_css again, find the correct block, and retry with a corrected old_str."
    )
    messages.insert(0, {"role": "system", "content": system_prompt})

    user_content = req.message or ""
    attachment_records = []
    vision_enabled = bool(getattr(model, "vision_enabled", False))
    ocr_strategy = app_config.settings.ocr_strategy

    if req.attachments:
        if not app_config.settings.file_upload_enabled:
            raise HTTPException(400, "File uploads are disabled by the admin.")

        content_parts = []
        if user_content.strip():
            content_parts.append({"type": "text", "text": user_content})

        extra_text_parts = []

        for att in req.attachments:
            att_filename = att.get("filename", "")
            att_path = att.get("file_path", "")
            att_type = (att.get("file_type", "") or "").lower()

            if att_path.startswith("/api/uploads/"):
                stored_name = att_path.split("/")[-1]
                full_path = os.path.join(app_config.settings.uploads_dir, stored_name)
            else:
                full_path = att_path

            record = {
                "filename": att_filename,
                "file_type": att_type,
                "file_path": att_path,
            }

            is_img = is_image_file(att_filename)

            if is_img:
                if vision_enabled:
                    try:
                        with open(full_path, "rb") as f:
                            img_data = base64.b64encode(f.read()).decode()
                        mime = att_type.lstrip(".")
                        if mime == "jpg":
                            mime = "jpeg"
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{mime};base64,{img_data}"},
                        })
                        record["image_included"] = True
                    except Exception as e:
                        content_parts.append({"type": "text", "text": f"\n[Failed to load image: {att_filename}]"})
                else:
                    if ocr_strategy == "deny":
                        raise HTTPException(400, "This model does not support image inputs. The admin has configured to deny image uploads for non-vision models. Use a vision-capable model or contact an admin to enable OCR.")
                    ocr_result = ocr_image(full_path)
                    if ocr_result:
                        extra_text_parts.append(f"\n--- OCR text from image '{att_filename}': ---\n{ocr_result}\n--- End OCR ---\n")
                        record["ocr_text"] = ocr_result[:500]
                    else:
                        extra_text_parts.append(f"\n[Image '{att_filename}' uploaded but no text could be extracted via OCR.]")
            else:
                result = process_uploaded_file(full_path, att_filename, force_ocr=False)
                if result.get("ocr_text"):
                    if att_type == ".pdf":
                        extra_text_parts.append(f"\n--- Content from PDF '{att_filename}': ---\n{result['ocr_text']}\n--- End PDF ---\n")
                    else:
                        extra_text_parts.append(f"\n--- Content from file '{att_filename}': ---\n{result['ocr_text']}\n--- End file ---\n")
                else:
                    extra_text_parts.append(f"\n[File '{att_filename}' uploaded (binary/unsupported format)]")

            attachment_records.append(record)

        if extra_text_parts:
            combined_text = "\n".join(extra_text_parts)
            content_parts.append({"type": "text", "text": combined_text})

        if len(content_parts) == 1 and content_parts[0]["type"] == "text":
            final_content = content_parts[0]["text"]
        elif vision_enabled:
            final_content = user_content
            if extra_text_parts:
                for part in extra_text_parts:
                    final_content = final_content + "\n" + part if final_content else part
        else:
            final_content = user_content
            if extra_text_parts:
                for part in extra_text_parts:
                    final_content = "\n".join([final_content, part]) if final_content else part

        attachments_json_str = json.dumps(attachment_records)
        user_msg = Message(
            conversation_id=req.conversation_id,
            role="user",
            content=final_content,
            attachments_json=attachments_json_str,
        )
        db.add(user_msg)
        await db.commit()

        if vision_enabled and any(p.get("type") == "image_url" for p in content_parts):
            user_msg_entry = {"role": "user", "content": content_parts}
        else:
            user_msg_entry = {"role": "user", "content": final_content}
        messages.append(user_msg_entry)
    else:
        user_msg = Message(conversation_id=req.conversation_id, role="user", content=req.message)
        db.add(user_msg)
        await db.commit()
        messages.append({"role": "user", "content": req.message})

    if getattr(model, "tools_enabled", True):
        tool_defs = get_tool_definitions()
        tools = [ToolDef(**t) for t in tool_defs]
    else:
        tools = []

    my_queue: asyncio.Queue = asyncio.Queue()

    async def run_ai_chat():
        async with async_session() as sess:
            streaming_msg_id = None
            try:
                accumulated_content = ""
                accumulated_reasoning = ""
                final_tool_calls = []
                total_prompt_tokens = 0
                total_completion_tokens = 0
                total_total_tokens = 0
                total_reasoning_tokens = 0

                draft = Message(
                    conversation_id=req.conversation_id,
                    role="assistant",
                    content="",
                    status="generating",
                )
                sess.add(draft)
                await sess.commit()
                streaming_msg_id = draft.id

                last_save_len = 0

                async def push_event(event_type: str, **kwargs):
                    evt = {"type": event_type, **kwargs}
                    _push_to_queues(req.conversation_id, f"data: {json.dumps(evt)}\n\n")

                async def save_draft_progress(force=False):
                    nonlocal last_save_len
                    if force or len(accumulated_content) - last_save_len >= 5:
                        draft.content = accumulated_content
                        await sess.commit()
                        last_save_len = len(accumulated_content)

                async for chunk in provider.stream_chat(messages, tools, model):
                    if chunk.content_delta and chunk.content_delta.strip():
                        accumulated_content += chunk.content_delta
                        await push_event("content_delta", content=chunk.content_delta)
                        await save_draft_progress()
                    if chunk.reasoning_content_delta:
                        accumulated_reasoning += chunk.reasoning_content_delta
                        await push_event("reasoning_delta", content=chunk.reasoning_content_delta)
                    if chunk.tool_calls is not None:
                        final_tool_calls = chunk.tool_calls
                    if chunk.usage:
                        total_prompt_tokens += chunk.usage["prompt_tokens"]
                        total_completion_tokens += chunk.usage["completion_tokens"]
                        total_total_tokens += chunk.usage["total_tokens"]
                    total_reasoning_tokens += chunk.reasoning_tokens

                tool_round = 0
                seen_tool_signatures: set[tuple] = set()
                while final_tool_calls and tool_round < 5:
                    tool_round += 1

                    current_sig = tuple(sorted(
                        (tc["name"], json.dumps(tc.get("arguments") or {}, sort_keys=True, default=str))
                        for tc in final_tool_calls
                    ))
                    if current_sig in seen_tool_signatures:
                        accumulated_content = (accumulated_content + "\n\n[Aborted: the same tool call failed twice in a row. Stop calling this tool and tell the user what went wrong.]").strip()
                        final_tool_calls = []
                        break
                    seen_tool_signatures.add(current_sig)

                    draft.content = accumulated_content or ""
                    draft.tool_calls_json = json.dumps([
                        {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                        for tc in final_tool_calls
                    ])
                    draft.reasoning_content = accumulated_reasoning or None
                    await sess.commit()

                    await push_event("tool_calls", tool_calls=final_tool_calls, content=accumulated_content)

                    tool_results = []
                    for tc in final_tool_calls:
                        await push_event("tool_start", name=tc["name"], id=tc["id"])
                        result = await execute_tool(tc["name"], tc["arguments"], _current_user=current_user)
                        tool_results.append({"tool_call_id": tc["id"], "tool_name": tc["name"], "content": result})
                        await push_event("tool_result", name=tc["name"], id=tc["id"], result=result)

                    assistant_entry = {
                        "role": "assistant",
                        "content": accumulated_content or "",
                        "tool_calls_json": json.dumps([
                            {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                            for tc in final_tool_calls
                        ]),
                    }
                    if accumulated_reasoning:
                        assistant_entry["reasoning_content"] = accumulated_reasoning
                    messages.append(assistant_entry)
                    for tr in tool_results:
                        tool_msg = Message(
                            conversation_id=req.conversation_id,
                            role="tool",
                            content=tr["content"],
                            tool_call_id=tr["tool_call_id"],
                            tool_name=tr["tool_name"],
                        )
                        sess.add(tool_msg)
                        await sess.commit()
                        messages.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "tool_name": tr["tool_name"], "content": tr["content"]})

                    accumulated_content = ""
                    accumulated_reasoning = ""
                    final_tool_calls = []
                    last_save_len = 0

                    # Always send tools so the model can retry after a failed
                    # tool call. The `while ... and tool_round < 5` loop already
                    # caps the number of rounds, so there's no infinite-loop risk.
                    next_tools = tools
                    async for chunk in provider.stream_chat_with_results(messages, next_tools, model, tool_results):
                        if chunk.content_delta and chunk.content_delta.strip():
                            accumulated_content += chunk.content_delta
                            await push_event("content_delta", content=chunk.content_delta)
                            await save_draft_progress()
                        if chunk.reasoning_content_delta:
                            accumulated_reasoning += chunk.reasoning_content_delta
                            await push_event("reasoning_delta", content=chunk.reasoning_content_delta)
                        if chunk.tool_calls is not None:
                            final_tool_calls = chunk.tool_calls
                        if chunk.usage:
                            total_prompt_tokens += chunk.usage["prompt_tokens"]
                            total_completion_tokens += chunk.usage["completion_tokens"]
                            total_total_tokens += chunk.usage["total_tokens"]
                        total_reasoning_tokens += chunk.reasoning_tokens

                if streaming_msg_id is not None:
                    draft.content = accumulated_content or ""
                    draft.reasoning_content = accumulated_reasoning or None
                    draft.status = "done"
                    draft.created_at = datetime.now(timezone.utc)
                    if final_tool_calls:
                        existing_tcs = []
                        if draft.tool_calls_json:
                            try:
                                existing_tcs = json.loads(draft.tool_calls_json)
                            except Exception:
                                existing_tcs = []
                        seen_ids = {tc.get("id") for tc in existing_tcs}
                        merged = list(existing_tcs)
                        for tc in final_tool_calls:
                            if tc["id"] not in seen_ids:
                                merged.append({"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]})
                        draft.tool_calls_json = json.dumps(merged) if merged else None
                    await sess.commit()
                else:
                    sess.add(Message(
                        conversation_id=req.conversation_id,
                        role="assistant",
                        content=accumulated_content or "",
                        reasoning_content=accumulated_reasoning or None,
                        status="done",
                    ))
                    await sess.commit()
                if accumulated_content:
                    await push_event("content", content=accumulated_content, reasoning_content=accumulated_reasoning or None)
                elif not final_tool_calls:
                    await push_event("error", error="Received an empty response from the model. Please verify your API key and model configuration.")

                if not db_messages and accumulated_content:
                    try:
                        title_msgs = [
                            {"role": "system", "content": "Generate a very short, concise title (maximum 6 words) for a conversation that starts with this message. Return ONLY the title, no quotes or explanations."},
                            {"role": "user", "content": accumulated_content}
                        ]
                        result = await provider.chat(title_msgs, [], model)
                        new_title = result.content.strip()[:255] or "New Chat"
                    except Exception:
                        new_title = accumulated_content[:80].replace("\n", " ") or "New Chat"
                    await sess.execute(
                        update(Conversation).where(Conversation.id == req.conversation_id).values(title=new_title)
                    )
                    await sess.commit()

                if total_total_tokens == 0:
                    total_total_tokens = max(1, len(req.message) // 4)
                    total_prompt_tokens = total_total_tokens
                try:
                    sess.add(TokenUsageLog(
                        user_id=current_user["user_id"],
                        model_id=model_id,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        reasoning_tokens=total_reasoning_tokens or None,
                        total_tokens=total_total_tokens,
                    ))
                    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                    await sess.execute(
                        sqlite_insert(UserModelUsage)
                        .values(user_id=current_user["user_id"], model_id=model_id, token_usage=total_total_tokens)
                        .on_conflict_do_update(
                            index_elements=["user_id", "model_id"],
                            set_={"token_usage": UserModelUsage.token_usage + total_total_tokens},
                        )
                    )
                    await sess.execute(
                        update(User)
                        .where(User.id == current_user["user_id"])
                        .values(token_usage=User.token_usage + total_total_tokens)
                    )
                    await sess.commit()
                except Exception as exc:
                    print(f"Token usage recording failed: {exc}", flush=True)

            except asyncio.CancelledError:
                try:
                    if streaming_msg_id is not None:
                        draft.content = accumulated_content
                        draft.reasoning_content = accumulated_reasoning or None
                        draft.status = "cancelled"
                        await sess.commit()
                except Exception:
                    pass
                raise
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                try:
                    await push_event("error", error=str(e))
                except Exception:
                    pass
                try:
                    if streaming_msg_id is not None:
                        draft.content = accumulated_content or error_msg
                        draft.reasoning_content = accumulated_reasoning or None
                        draft.status = "done"
                        await sess.commit()
                except Exception:
                    pass
            finally:
                _cleanup_generation(req.conversation_id)

    if req.conversation_id in active_generations:
        raise HTTPException(409, "A generation is already in progress for this conversation")

    my_queue_final = my_queue
    active_generations[req.conversation_id] = {
        "queues": {my_queue_final},
        "task": None,
    }
    task = asyncio.create_task(run_ai_chat())
    active_generations[req.conversation_id]["task"] = task

    async def event_stream():
        try:
            while True:
                event = await my_queue.get()
                if event is None:
                    break
                yield event
                if event == "data: [DONE]\n\n":
                    break
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/chat/resume/{conv_id}")
async def chat_resume(conv_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(404, "Conversation not found")

    gen = active_generations.get(conv_id)
    if not gen:
        result = await db.execute(
            select(Message).where(Message.conversation_id == conv_id, Message.status == "generating").order_by(Message.id.desc()).limit(1)
        )
        draft = result.scalar_one_or_none()
        if draft:
            draft.status = "interrupted"
            await db.commit()
            content_event: dict = {"type": "content", "content": draft.content or ""}
            if draft.tool_calls_json:
                content_event["tool_calls"] = json.loads(draft.tool_calls_json)
            if draft.reasoning_content:
                content_event["reasoning_content"] = draft.reasoning_content

            async def stale_stream():
                yield f"data: {json.dumps(content_event)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stale_stream(), media_type="text/event-stream")
        raise HTTPException(404, "No active generation for this conversation")

    my_queue: asyncio.Queue = asyncio.Queue()
    gen["queues"].add(my_queue)

    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id, Message.status == "generating").order_by(Message.id.desc()).limit(1)
    )
    draft = result.scalar_one_or_none()
    if draft:
        catchup = {"type": "content", "content": draft.content or ""}
        if draft.tool_calls_json:
            catchup["tool_calls"] = json.loads(draft.tool_calls_json)
        if draft.reasoning_content:
            catchup["reasoning_content"] = draft.reasoning_content
        try:
            my_queue.put_nowait(f"data: {json.dumps(catchup)}\n\n")
        except asyncio.QueueFull:
            pass

    async def resume_stream():
        try:
            while True:
                event = await my_queue.get()
                if event is None:
                    break
                yield event
                if event == "data: [DONE]\n\n":
                    break
        except asyncio.CancelledError:
            pass
        finally:
            gen["queues"].discard(my_queue)

    return StreamingResponse(resume_stream(), media_type="text/event-stream")


@router.post("/chat/cancel/{conv_id}")
async def chat_cancel(conv_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(404, "Conversation not found")

    gen = active_generations.get(conv_id)
    if gen:
        gen["task"].cancel()
        _push_to_queues(conv_id, "data: [DONE]\n\n")
        return {"status": "cancelled"}

    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id, Message.status == "generating")
    )
    draft = result.scalar_one_or_none()
    if draft:
        async with async_session() as sess:
            result = await sess.execute(
                select(Message).where(Message.id == draft.id)
            )
            msg = result.scalar_one_or_none()
            if msg:
                msg.status = "cancelled"
                await sess.commit()
        return {"status": "cancelled"}

    return {"status": "no_active_generation"}


@router.get("/conversations/{conv_id}/generation-status", response_model=GenerateStatusResponse)
async def generation_status(conv_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == current_user["user_id"])
    )
    if not conv_result.scalar_one_or_none():
        raise HTTPException(404, "Conversation not found")

    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id.desc()).limit(1)
    )
    latest = result.scalar_one_or_none()

    if latest and latest.status == "generating":
        return GenerateStatusResponse(
            generating=True,
            message_id=latest.id,
            message_content=latest.content,
            tool_calls_json=json.loads(latest.tool_calls_json) if latest.tool_calls_json else None,
            reasoning_content=latest.reasoning_content,
            status=latest.status,
        )

    return GenerateStatusResponse(generating=False)


# --- Image Generation ---

@router.post("/chat/image", response_model=ImageGenerationResponse)
async def generate_image(req: ImageGenerationRequest, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == req.conversation_id, Conversation.user_id == current_user["user_id"])
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(404, "Conversation not found")

    model_result = await db.execute(select(ModelConfig).where(ModelConfig.id == req.model_id))
    model = model_result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    if not model.enabled:
        raise HTTPException(400, "Model is disabled")
    model_type = getattr(model, "model_type", "chat") or "chat"
    if model_type != "image":
        raise HTTPException(400, "This model is not an image generation model")

    provider = get_provider(model.provider)
    if not provider:
        raise HTTPException(400, f"Unknown provider: {model.provider}")

    if model.api_key_env:
        env_attr = model.api_key_env.lower()
        key_val = getattr(app_config.settings, env_attr, None)
        if not key_val:
            raise HTTPException(400, f"API key not configured for {model.name}")

    user_result = await db.execute(select(User).where(User.id == current_user["user_id"]))
    user = user_result.scalar_one_or_none()

    subscription_image_limit = None
    model_sub_image_limit = None
    subscription_result = await db.execute(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id)
        .where(
            UserSubscription.user_id == current_user["user_id"],
            UserSubscription.status == "active",
        )
        .where(
            (UserSubscription.expires_at > datetime.now(timezone.utc))
            | (UserSubscription.expires_at == None)
        )
    )
    active_sub_row = subscription_result.first()
    free_plan = None
    free_plan_result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "Free")
    )
    free_plan = free_plan_result.scalar_one_or_none()

    if active_sub_row:
        sub, plan = active_sub_row
        sub_image_limit_val = getattr(plan, "image_limit", None)
        if sub_image_limit_val is not None:
            subscription_image_limit = sub_image_limit_val
        model_limit_result = await db.execute(
            select(PlanModelLimit).where(
                PlanModelLimit.plan_id == plan.id,
                PlanModelLimit.model_id == req.model_id,
            )
        )
        model_limit = model_limit_result.scalar_one_or_none()
        if model_limit and getattr(model_limit, "image_limit", None) is not None:
            model_sub_image_limit = model_limit.image_limit
    else:
        if free_plan:
            free_image_limit = getattr(free_plan, "image_limit", None)
            if free_image_limit is not None:
                subscription_image_limit = free_image_limit
            model_limit_result = await db.execute(
                select(PlanModelLimit).where(
                    PlanModelLimit.plan_id == free_plan.id,
                    PlanModelLimit.model_id == req.model_id,
                )
            )
            model_limit = model_limit_result.scalar_one_or_none()
            if model_limit and getattr(model_limit, "image_limit", None) is not None:
                model_sub_image_limit = model_limit.image_limit

    global_image_limit = None
    if user and getattr(user, "image_limit", None) is not None:
        global_image_limit = user.image_limit
    if subscription_image_limit is not None:
        global_image_limit = subscription_image_limit if global_image_limit is None else min(global_image_limit, subscription_image_limit)

    if model_sub_image_limit is not None:
        per_model_result = await db.execute(
            select(UserModelUsage).where(
                UserModelUsage.user_id == current_user["user_id"],
                UserModelUsage.model_id == req.model_id,
            )
        )
        per_model_row = per_model_result.scalar_one_or_none()
        per_model_img_usage = per_model_row.image_usage if per_model_row else 0
        if per_model_img_usage >= model_sub_image_limit * req.n:
            raise HTTPException(403, f"Image limit reached for this model ({per_model_img_usage}/{model_sub_image_limit}). Upgrade your plan or contact an admin.")

    user_image_usage = getattr(user, "image_usage", 0) or 0
    if global_image_limit is not None and user_image_usage >= global_image_limit * req.n:
        raise HTTPException(403, f"Image limit reached ({user_image_usage}/{global_image_limit}). Upgrade your plan or contact an admin.")

    user_prompt_msg = Message(conversation_id=req.conversation_id, role="user", content=f"[Image Generation] {req.prompt}")
    db.add(user_prompt_msg)
    await db.commit()

    try:
        result = await provider.generate_image(req.prompt, model, req.size, req.n)
    except NotImplementedError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Image generation failed: {str(e)}")

    assistant_msg = Message(
        conversation_id=req.conversation_id,
        role="assistant",
        content=json.dumps({"images": result.images, "revised_prompt": result.revised_prompt, "prompt": req.prompt, "size": req.size}),
    )
    db.add(assistant_msg)
    await db.commit()

    try:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        await db.execute(
            sqlite_insert(UserModelUsage)
            .values(user_id=current_user["user_id"], model_id=req.model_id, image_usage=req.n)
            .on_conflict_do_update(
                index_elements=["user_id", "model_id"],
                set_={"image_usage": UserModelUsage.image_usage + req.n},
            )
        )
        await db.execute(
            update(User)
            .where(User.id == current_user["user_id"])
            .values(image_usage=User.image_usage + req.n)
        )
        await db.commit()
    except Exception:
        pass

    return ImageGenerationResponse(images=result.images, revised_prompt=result.revised_prompt)

@router.get("/files/{filename}")
async def serve_file(filename: str, current_user: dict = Depends(get_current_user)):
    safe_name = os.path.basename(filename)
    filepath = os.path.join("data/documents", safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(404, "File not found")
    real = os.path.realpath(filepath)
    docs_dir = os.path.realpath("data/documents")
    if not real.startswith(docs_dir):
        raise HTTPException(404, "File not found")
    return FileResponse(filepath, filename=safe_name)


# --- Tool info ---

@router.get("/tools")
async def list_tools(current_user: dict = Depends(get_current_user)):
    return get_tool_definitions()


app.include_router(router)


# Serve static frontend
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}", response_class=HTMLResponse)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("data/"):
            raise HTTPException(404)
        index_path = os.path.join(static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path) as f:
                return HTMLResponse(f.read())
        raise HTTPException(404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
