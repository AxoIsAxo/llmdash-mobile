import asyncio
import base64
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Depends, HTTPException, APIRouter, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from sqlalchemy import select, delete, update, func
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from . import config as app_config
from .database import init_db, get_db, async_session, ModelConfig, Conversation, Message, User, TokenUsageLog, UserModelUsage, SubscriptionPlan, PlanModelLimit, UserSubscription, Document
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
from .skills import register_builtins
from .skills.registry import skill_registry
from .skills.integration import build_system_prompt
from .config_file import ConfigFileManager
from .sse import active_generations, push_to_queues, cleanup_generation
from .model_capabilities import detect_audio_enabled
from .sandbox import is_docker_available
from .ocr import process_uploaded_file, is_allowed_file, is_image_file, is_audio_file, ocr_image, is_ocr_available
from .whisper_stt import transcribe_audio, transcribe_audio_openrouter, VALID_PROVIDERS, DEFAULT_PROVIDER, DEFAULT_OPENROUTER_MODEL, VALID_MODEL_SIZES, VALID_COMPUTE_TYPES, get_model as get_whisper_model
from .audio_convert import prepare_audio_for_provider
from .routers.auth import router as auth_router, get_current_user, require_role, load_provider_configs
from .routers.subscriptions import router as subscriptions_router

router = APIRouter(prefix="/api")


def get_active_provider_configs():
    return load_provider_configs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    register_builtins()
    async with async_session() as sess:
        result = await sess.execute(
            select(Message).where(Message.status == "generating")
        )
        stuck = result.scalars().all()
        for msg in stuck:
            msg.status = "interrupted"
        if stuck:
            await sess.commit()

    # Warm up the local Whisper model in the background so the first voice
    # message doesn't block on a multi-hundred-MB download mid-request.
    if (getattr(app_config.settings, "whisper_provider", "local") or "local").lower() == "local":
        async def _warmup_whisper():
            try:
                await asyncio.to_thread(get_whisper_model)
            except Exception as e:
                logger.warning(f"Whisper warmup failed: {e}")
        asyncio.create_task(_warmup_whisper())

    yield


app = FastAPI(title="LLMDash", version="1.0.0", lifespan=lifespan)

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
app.state.limiter = limiter
app.add_exception_handler(429, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
from .skills.router import router as skills_router
app.include_router(skills_router)


# --- Model Config (admin only) ---

@router.get("/models", response_model=list[ModelConfigResponse])
async def list_models(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(ModelConfig).order_by(
        func.coalesce(ModelConfig.sort_order, 2**31 - 1), ModelConfig.id
    )
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
            audio_enabled=bool(getattr(m, "audio_enabled", False)),
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
        audio_enabled=getattr(cfg, "audio_enabled", False),
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
    if len(req.model_ids) != len(set(req.model_ids)):
        raise HTTPException(400, "model_ids must not contain duplicates")
    result = await db.execute(select(ModelConfig.id).where(ModelConfig.id.in_(req.model_ids)))
    found = {row[0] for row in result.all()}
    missing = set(req.model_ids) - found
    if missing:
        raise HTTPException(400, f"Unknown model ids: {sorted(missing)}")
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
    "counterfeit", "aingdiffusion", "deliberate", "dreamlike",
    "pfg-art", "citrine-dream", "photonic", "realism-engine", "aniverse",
    "samaritan", "pixar-", "retro-", "hasdx",
    "image", "img-gen", "txt2img", "img2img",
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


def _detect_audio_capability(model_id: str, raw_entry: dict | None = None) -> bool:
    """Auto-detect audio input support from the provider's model metadata.

    Reads `architecture.input_modalities` (OpenRouter-style) or
    `architecture.modalities` (other providers) and returns True if "audio"
    appears. No hardcoded pattern list — the provider is the source of truth.
    Returns False if the metadata is missing or doesn't mention audio.
    """
    if raw_entry:
        arch = raw_entry.get("architecture", {}) or {}
        in_mods = arch.get("input_modalities", []) or arch.get("modalities", []) or []
        if any("audio" in str(m).lower() for m in in_mods):
            return True
        if raw_entry.get("input_audio") or raw_entry.get("supports_audio"):
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
                                "supports_audio": _detect_audio_capability(mid, m),
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
                                        "supports_audio": _detect_audio_capability(mid, m),
                                    })
                    except Exception:
                        pass
            elif pc["type"] == "anthropic":
                anthro_base = pc.get("base_url") or "https://api.anthropic.com"
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        f"{anthro_base.rstrip('/')}/v1/models",
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
                                "supports_audio": _detect_audio_capability(mid),
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
        audio_enabled=bool(getattr(model, "audio_enabled", False)),
        tools_enabled=bool(getattr(model, "tools_enabled", True)),
        enabled=model.enabled,
        sort_order=getattr(model, "sort_order", None),
        created_at=model.created_at.isoformat() if model.created_at else "",
        updated_at=model.updated_at.isoformat() if model.updated_at else "",
    )


async def _probe_model_capabilities(model, db: AsyncSession) -> dict:
    """Probe the provider's /models endpoint for the saved model_name and
    return autodetected vision + audio capabilities. No pattern matching —
    the provider's architecture block is the source of truth.
    """
    if model.provider != "openai_compatible" or not model.base_url:
        return {"audio": False, "vision": False, "source": "unsupported_provider"}
    api_key = ""
    if model.api_key_env:
        api_key = getattr(app_config.settings, model.api_key_env.lower(), None) or ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{model.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code != 200:
                return {"audio": False, "vision": False, "source": f"http_{resp.status_code}"}
            data = resp.json()
            raw_list = data.get("data", data.get("models", []))
            entry = None
            for m in raw_list:
                mid = m.get("id", m.get("name", ""))
                if mid == model.model_name or mid.endswith(f"/{model.model_name}"):
                    entry = m
                    break
            if entry is None:
                return {"audio": False, "vision": False, "source": "model_not_in_provider_list"}
            arch = entry.get("architecture", {}) or {}
            in_mods = [str(m).lower() for m in (arch.get("input_modalities", []) or arch.get("modalities", []) or [])]
            return {
                "audio": "audio" in in_mods or bool(entry.get("input_audio")) or bool(entry.get("supports_audio")),
                "vision": "image" in in_mods or "vision" in in_mods or bool(entry.get("supports_vision")),
                "input_modalities": list(arch.get("input_modalities", []) or []),
                "source": "provider",
            }
    except Exception as e:
        return {"audio": False, "vision": False, "source": f"error:{e}"}


@router.get("/models/{model_id}/detect-capabilities")
async def detect_model_capabilities(model_id: int, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    """Probe the provider's /models endpoint for the saved model_name and
    return autodetected vision + audio capabilities. No pattern matching —
    the provider's architecture block is the source of truth.
    """
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    return await _probe_model_capabilities(model, db)


@router.post("/models/{model_id}/auto-enable")
async def auto_enable_capabilities(model_id: int, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    """Probe the provider and flip audio_enabled / vision_enabled on the
    stored model config based on the autodetected capabilities. Idempotent.
    """
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    cap = await _probe_model_capabilities(model, db)
    changed = []
    if cap.get("audio") and not bool(getattr(model, "audio_enabled", False)):
        model.audio_enabled = True
        changed.append("audio_enabled")
    if cap.get("vision") and not bool(getattr(model, "vision_enabled", False)):
        model.vision_enabled = True
        changed.append("vision_enabled")
    if changed:
        await db.commit()
    return {"changed": changed, "capabilities": cap}


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
    allowed = set(app_config.ENV_VAR_MAP.keys())
    invalid = set(req.updates.keys()) - allowed
    if invalid:
        raise HTTPException(400, f"Unknown environment variables: {', '.join(sorted(invalid))}")
    existing = ConfigFileManager.read("data/.env")
    existing.update(req.updates)
    ConfigFileManager.write("data/.env", existing)
    app_config.reload_settings()
    return {"status": "updated", "updated_keys": list(req.updates.keys())}


@router.get("/config/status")
async def config_status(current_user: dict = Depends(get_current_user)):
    docker_ok = is_docker_available()
    from importlib.util import find_spec
    doc_ok = all(find_spec(m) for m in ("docx", "fpdf", "odf"))
    return {
        "docker_available": docker_ok,
        "tools": {
            "web_search": bool(app_config.settings.searxng_url),
            "document_editor": doc_ok,
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
        whisper_provider=app_config.settings.whisper_provider,
        whisper_openrouter_model=app_config.settings.whisper_openrouter_model,
    )


@router.put("/config/uploads", response_model=FileUploadSettings)
async def update_upload_settings(req: FileUploadSettingsUpdate, current_user: dict = Depends(require_role("owner", "admin"))):
    updates = {}
    if req.file_upload_enabled is not None:
        updates["FILE_UPLOAD_ENABLED"] = str(req.file_upload_enabled).lower()
    if req.ocr_enabled is not None:
        updates["OCR_ENABLED"] = str(req.ocr_enabled).lower()
    if req.ocr_strategy is not None:
        if req.ocr_strategy not in ("ocr", "deny"):
            raise HTTPException(400, "ocr_strategy must be 'ocr' or 'deny'")
        updates["OCR_STRATEGY"] = req.ocr_strategy
    if req.whisper_model is not None:
        if req.whisper_model not in VALID_MODEL_SIZES:
            raise HTTPException(400, f"Invalid whisper model '{req.whisper_model}'. Must be one of: {', '.join(VALID_MODEL_SIZES)}")
        updates["WHISPER_MODEL"] = req.whisper_model
    if req.whisper_compute_type is not None:
        if req.whisper_compute_type not in VALID_COMPUTE_TYPES:
            raise HTTPException(400, f"Invalid whisper compute type '{req.whisper_compute_type}'. Must be one of: {', '.join(VALID_COMPUTE_TYPES)}")
        updates["WHISPER_COMPUTE_TYPE"] = req.whisper_compute_type
    if req.whisper_device is not None:
        updates["WHISPER_DEVICE"] = req.whisper_device
    if req.whisper_language is not None:
        updates["WHISPER_LANGUAGE"] = req.whisper_language
    if req.whisper_beam_size is not None:
        updates["WHISPER_BEAM_SIZE"] = str(int(req.whisper_beam_size))
    if req.whisper_provider is not None:
        provider_value = str(req.whisper_provider).strip().lower()
        if provider_value not in VALID_PROVIDERS:
            raise HTTPException(400, f"Invalid whisper provider '{req.whisper_provider}'. Must be one of: {', '.join(VALID_PROVIDERS)}")
        updates["WHISPER_PROVIDER"] = provider_value
    if req.whisper_openrouter_model is not None:
        openrouter_model = str(req.whisper_openrouter_model).strip()
        if not openrouter_model:
            raise HTTPException(400, "whisper_openrouter_model cannot be empty")
        updates["WHISPER_OPENROUTER_MODEL"] = openrouter_model

    if updates:
        ConfigFileManager.update("data/.env", updates)
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
        whisper_provider=app_config.settings.whisper_provider,
        whisper_openrouter_model=app_config.settings.whisper_openrouter_model,
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
    provider = app_config.settings.whisper_provider or DEFAULT_PROVIDER
    if provider not in VALID_PROVIDERS:
        provider = DEFAULT_PROVIDER
    return {
        "model": model,
        "compute_type": compute_type,
        "device": app_config.settings.whisper_device or "auto",
        "language": app_config.settings.whisper_language,
        "beam_size": beam,
        "provider": provider,
        "openrouter_model": app_config.settings.whisper_openrouter_model or DEFAULT_OPENROUTER_MODEL,
        "openrouter_configured": bool(app_config.settings.openrouter_api_key),
        "available_models": list(VALID_MODEL_SIZES),
        "available_compute_types": list(VALID_COMPUTE_TYPES),
        "available_providers": list(VALID_PROVIDERS),
    }


@router.post("/chat/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...), request: Request = None, current_user: dict = Depends(get_current_user)):
    if not app_config.settings.file_upload_enabled:
        raise HTTPException(403, "File uploads are disabled by the admin")

    if not file.filename:
        raise HTTPException(400, "No filename provided")

    max_size = 20 * 1024 * 1024
    if request is not None:
        try:
            declared = int(request.headers.get("content-length") or 0)
            if declared > max_size:
                raise HTTPException(413, f"File too large (max {max_size // (1024*1024)}MB)")
        except ValueError:
            pass

    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file.filename)
    if not is_allowed_file(safe_name):
        raise HTTPException(400, "File type not allowed")

    uploads_dir = app_config.settings.uploads_dir
    os.makedirs(uploads_dir, exist_ok=True)

    file_id = uuid.uuid4().hex[:12]
    stored_name = f"{file_id}_{safe_name}"
    file_path = os.path.join(uploads_dir, stored_name)

    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(413, f"File too large (max {max_size // (1024*1024)}MB)")

    with open(file_path, "wb") as f:
        f.write(content)

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
    provider = (getattr(app_config.settings, "whisper_provider", None) or DEFAULT_PROVIDER).strip().lower()
    if provider not in VALID_PROVIDERS:
        provider = DEFAULT_PROVIDER
    try:
        if provider == "openrouter":
            filename = file.filename or "audio.webm"
            content_type = file.content_type or "audio/webm"
            text = transcribe_audio_openrouter(content, filename=filename, content_type=content_type)
        else:
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

    if req.conversation_id in active_generations:
        raise HTTPException(409, "A generation is already in progress for this conversation")

    model_id = req.model_id or conversation.model_id
    if not model_id:
        raise HTTPException(400, "No model configured for this conversation")

    if req.model_id and conversation.model_id != req.model_id:
        conversation.model_id = req.model_id
        await db.commit()

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

    system_prompt = build_system_prompt(model.name)
    messages.insert(0, {"role": "system", "content": system_prompt})

    user_content = req.message or ""
    attachment_records = []
    vision_enabled = bool(getattr(model, "vision_enabled", False))
    audio_enabled = await detect_audio_enabled(model, app_config)
    ocr_strategy = app_config.settings.ocr_strategy
    whisper_provider_setting = (getattr(app_config.settings, "whisper_provider", None) or DEFAULT_PROVIDER).strip().lower()
    if whisper_provider_setting not in VALID_PROVIDERS:
        whisper_provider_setting = DEFAULT_PROVIDER

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
                stored_name = os.path.basename(att_path.split("/")[-1])
                full_path = os.path.join(app_config.settings.uploads_dir, stored_name)
            else:
                # Security: never open client-supplied paths. Attachments must
                # reference files previously uploaded through /api/chat/upload.
                raise HTTPException(400, "Invalid attachment path. Attachments must reference uploaded files.")

            if not os.path.isfile(full_path):
                raise HTTPException(400, f"Attachment file not found: {att_filename}")

            record = {
                "filename": att_filename,
                "file_type": att_type,
                "file_path": att_path,
            }

            is_img = is_image_file(att_filename)
            is_audio = is_audio_file(att_filename)

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
                    except Exception:
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
            elif is_audio:
                if audio_enabled and model.provider == "openai_compatible":
                    with open(full_path, "rb") as f:
                        audio_bytes = f.read()
                    try:
                        prepared_bytes, audio_format = prepare_audio_for_provider(
                            audio_bytes, filename=att_filename
                        )
                        if prepared_bytes is None or audio_format is None:
                            logger.error(
                                f"Audio embed skipped for {att_filename}: "
                                "transcoding to wav failed (tried PyAV + ffmpeg)."
                            )
                            extra_text_parts.append(
                                f"\n[Audio file '{att_filename}' could not be transcoded for the model.]"
                            )
                        else:
                            b64 = base64.b64encode(prepared_bytes).decode("ascii")
                            logger.info(
                                f"chat audio embed: model={model.model_name} "
                                f"format={audio_format} b64_len={len(b64)}"
                            )
                            content_parts.append({
                                "type": "input_audio",
                                "input_audio": {
                                    "data": b64,
                                    "format": audio_format,
                                },
                            })
                            record["audio_included"] = True
                            record["audio_format"] = audio_format
                    except Exception as e:
                        logger.error(f"Failed to embed audio {att_filename}: {e}")
                        extra_text_parts.append(
                            f"\n[Audio file '{att_filename}' could not be embedded: {e}]"
                        )
                else:
                    try:
                        with open(full_path, "rb") as f:
                            audio_bytes = f.read()
                        if whisper_provider_setting == "openrouter":
                            ct = att_type.lstrip(".") or "webm"
                            mime = f"audio/{ct if ct else 'webm'}"
                            whisper_text = transcribe_audio_openrouter(
                                audio_bytes, filename=att_filename, content_type=mime
                            )
                        else:
                            whisper_text = transcribe_audio(audio_bytes)
                    except Exception as e:
                        whisper_text = None
                        logger.warning(f"Failed to transcribe audio {att_filename}: {e}")

                    if whisper_text:
                        record["transcription"] = whisper_text[:500]
                        extra_text_parts.append(
                            f"\n--- Transcription of audio '{att_filename}': ---\n{whisper_text}\n--- End transcription ---\n"
                        )
                    else:
                        extra_text_parts.append(
                            f"\n[Audio '{att_filename}' uploaded but no speech could be transcribed.]"
                        )
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

        has_multimodal = any(
            p.get("type") in ("image_url", "input_audio", "audio_url")
            for p in content_parts
        )

        if len(content_parts) == 1 and content_parts[0]["type"] == "text":
            final_content = content_parts[0]["text"]
        elif has_multimodal:
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

        if has_multimodal:
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
        tool_defs = skill_registry.get_tool_definitions()
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
                    push_to_queues(req.conversation_id, f"data: {json.dumps(evt)}\n\n")

                async def save_draft_progress(force=False):
                    nonlocal last_save_len
                    if force or len(accumulated_content) - last_save_len >= 5:
                        draft.content = accumulated_content
                        await sess.commit()
                        last_save_len = len(accumulated_content)

                def _looks_like_audio_error(err: Exception) -> bool:
                    msg = (str(err) or "").lower()
                    keywords = (
                        "audio", "input_audio", "audio_url", "audio format",
                        "audio data", "audio input", "audio content",
                        "invalid audio", "unsupported audio",
                    )
                    return any(k in msg for k in keywords)

                chat_messages = messages
                try:
                    stream_iter = provider.stream_chat(chat_messages, tools, model)
                except Exception as e:
                    if audio_enabled and _looks_like_audio_error(e):
                        logger.error(
                            f"Audio embed rejected by model. The model advertises "
                            f"audio support but the upstream rejected the payload. "
                            f"Underlying error: {e}"
                        )
                    raise

                async for chunk in stream_iter:
                    if chunk.content_delta:
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
                consecutive_search_failures = 0

                def _looks_like_filler(text: str) -> bool:
                    if not text or len(text) < 5:
                        return False
                    stripped = text.strip().lower()
                    filler_patterns = (
                        r"^(let me\s+(try|search|look|find|check|see|attempt|google|query))",
                        r"^(i('ll| will)\s+(try|search|look|find|check|see|attempt|google|query|use))",
                        r"^(searching|looking|trying|checking)",
                        r"^(i can\s+(try|search|look|find|check))",
                        r"^(perhaps\s+i\s+(should|can|could|need to))",
                        r"^(maybe\s+i\s+(should|can|could|need to))",
                        r"^(ok,?\s+)?(hold on|one moment|give me)",
                    )
                    import re as _re
                    for pat in filler_patterns:
                        if _re.match(pat, stripped):
                            return True
                    if len(stripped) <= 60 and not any(
                        kw in stripped for kw in ("result", "found", "here", "page", "according", "showing")
                    ):
                        if any(kw in stripped for kw in ("search", "searching", "try", "trying", "find", "looking", "look", "scrape", "scraping", "fetch")):
                            return True
                    return False

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

                    draft.content = accumulated_content or draft.content
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
                        result = await skill_registry.execute(tc["name"], tc["arguments"], _current_user=current_user)
                        tool_results.append({"tool_call_id": tc["id"], "tool_name": tc["name"], "content": result})
                        await push_event("tool_result", name=tc["name"], id=tc["id"], result=result)

                    search_tool_names = frozenset(("web_search", "web_scrape"))
                    for tr in tool_results:
                        if tr["tool_name"] in search_tool_names and tr["content"].startswith("__TOOL_ERROR__:"):
                            consecutive_search_failures += 1
                            break
                    else:
                        consecutive_search_failures = 0

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

                    accumulated_reasoning = ""
                    final_tool_calls = []
                    last_save_len = 0
                    content_len_before_round = len(accumulated_content)

                    # Always send tools so the model can retry after a failed
                    # tool call. The `while ... and tool_round < 5` loop already
                    # caps the number of rounds, so there's no infinite-loop risk.
                    next_tools = tools
                    async for chunk in provider.stream_chat_with_results(messages, next_tools, model, tool_results):
                        if chunk.content_delta:
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

                    if final_tool_calls:
                        new_content = accumulated_content[content_len_before_round:].strip()
                        if _looks_like_filler(new_content):
                            accumulated_content = accumulated_content[:content_len_before_round]

                    if final_tool_calls and consecutive_search_failures >= 3:
                        new_content = accumulated_content[content_len_before_round:].strip()
                        if _looks_like_filler(new_content) or not new_content:
                            accumulated_content = accumulated_content[:content_len_before_round]
                        accumulated_content = (accumulated_content + "\n\n[Aborted: search and scraping tools failed 3 times in a row. Tell the user what happened and stop.]").strip()
                        final_tool_calls = []
                        break

                if streaming_msg_id is not None:
                    draft.content = accumulated_content or draft.content
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
                else:
                    await push_event("content", content=draft.content or accumulated_content or "", reasoning_content=accumulated_reasoning or None)

                if not db_messages and accumulated_content:
                    try:
                        title_source = (req.message or "").strip() or accumulated_content[:200]
                        title_msgs = [
                            {"role": "system", "content": "Generate a very short, concise title (maximum 6 words) for a conversation that starts with this message. Return ONLY the title, no quotes or explanations."},
                            {"role": "user", "content": title_source}
                        ]
                        result = await provider.chat(title_msgs, [], model)
                        new_title = result.content.strip()[:255] or "New Chat"
                    except Exception:
                        new_title = title_source[:80].replace("\n", " ") or "New Chat"
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
                cleanup_generation(req.conversation_id)

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
        push_to_queues(conv_id, "data: [DONE]\n\n")
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
            raise HTTPException(403, f"Image limit reached for this model ({per_model_img_usage}/{model_sub_image_limit * req.n}). Upgrade your plan or contact an admin.")

    user_image_usage = getattr(user, "image_usage", 0) or 0
    if global_image_limit is not None and user_image_usage >= global_image_limit * req.n:
        raise HTTPException(403, f"Image limit reached ({user_image_usage}/{global_image_limit * req.n}). Upgrade your plan or contact an admin.")

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

# --- Document Management ---

def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path) if path and os.path.exists(path) else 0
    except OSError:
        return 0


@router.get("/documents")
async def list_documents(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document)
        .where(Document.user_id == current_user["user_id"])
        .order_by(Document.updated_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "format": d.format,
            "version": d.version,
            "file_path": d.file_path,
            "file_size": _file_size(d.file_path),
            "content": d.content_md,
            "created_at": d.created_at.isoformat() if d.created_at else "",
            "updated_at": d.updated_at.isoformat() if d.updated_at else "",
        }
        for d in docs
    ]


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.user_id == current_user["user_id"])
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    return {
        "id": doc.id,
        "filename": doc.filename,
        "format": doc.format,
        "version": doc.version,
        "file_path": doc.file_path,
        "file_size": _file_size(doc.file_path),
        "content": doc.content_md,
        "created_at": doc.created_at.isoformat() if doc.created_at else "",
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else "",
    }


@router.get("/documents/{doc_id}/download")
async def download_document(doc_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.user_id == current_user["user_id"])
    )
    doc = result.scalar_one_or_none()
    if not doc or not os.path.exists(doc.file_path):
        raise HTTPException(404, "Document not found")
    return FileResponse(doc.file_path, filename=os.path.basename(doc.file_path))


@router.put("/documents/{doc_id}")
async def update_document_meta(doc_id: int, body: dict, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.user_id == current_user["user_id"])
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if "filename" in body:
        doc.filename = body["filename"]
    doc.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated"}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.user_id == current_user["user_id"])
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    await db.delete(doc)
    await db.commit()
    return {"status": "deleted"}


@router.get("/documents/{doc_id}/versions")
async def list_document_versions(doc_id: int, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    doc_result = await db.execute(
        select(Document).where(Document.id == doc_id, Document.user_id == current_user["user_id"])
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    result = await db.execute(
        select(Document)
        .where(
            Document.user_id == current_user["user_id"],
            Document.filename == doc.filename,
            Document.format == doc.format,
        )
        .order_by(Document.version.desc())
    )
    versions = result.scalars().all()
    return [
        {
            "id": d.id,
            "version": d.version,
            "file_path": d.file_path,
            "file_size": _file_size(d.file_path),
            "content": d.content_md,
            "created_at": d.created_at.isoformat() if d.created_at else "",
        }
        for d in versions
    ]


@router.post("/documents/upload")
async def upload_document(file: UploadFile = File(...), current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not file.filename:
        raise HTTPException(400, "No filename provided")
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file.filename)
    uploads_dir = app_config.settings.uploads_dir
    os.makedirs(uploads_dir, exist_ok=True)
    file_id = uuid.uuid4().hex[:12]
    stored_name = f"{file_id}_{safe_name}"
    file_path = os.path.join(uploads_dir, stored_name)
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 20MB)")
    with open(file_path, "wb") as f:
        f.write(content)
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else "txt"
    base_name = safe_name.rsplit(".", 1)[0] if "." in safe_name else safe_name

    extracted_md = ""
    if ext == "docx":
        try:
            from docx import Document as DocxDoc
            d = DocxDoc(file_path)
            extracted_md = "\n\n".join(p.text for p in d.paragraphs if p.text.strip())
        except Exception:
            extracted_md = f"[Uploaded document: {safe_name}]"
    elif ext == "pdf":
        try:
            import fitz
            d = fitz.open(file_path)
            extracted_md = "\n\n".join(page.get_text() for page in d)
        except Exception:
            extracted_md = f"[Uploaded PDF: {safe_name}]"
    elif ext == "odt":
        try:
            from odf.opendocument import OpenDocumentText
            d = OpenDocumentText(file_path)
            texts = []
            for elem in d.text.childNodes:
                if hasattr(elem, "childNodes"):
                    for child in elem.childNodes:
                        if hasattr(child, "data") and child.data:
                            texts.append(child.data)
            extracted_md = "\n\n".join(texts)
        except Exception:
            extracted_md = f"[Uploaded ODT: {safe_name}]"
    elif ext == "tex":
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            extracted_md = f.read()
    elif ext == "md":
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            extracted_md = f.read()
    else:
        extracted_md = f"[Uploaded file: {safe_name}]"

    doc = Document(
        user_id=current_user["user_id"],
        filename=base_name,
        format=ext,
        version=1,
        file_path=file_path,
        content_md=extracted_md,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    return {
        "id": doc.id,
        "filename": doc.filename,
        "format": doc.format,
        "version": doc.version,
        "file_path": file_path,
        "content_md": extracted_md,
        "created_at": doc.created_at.isoformat() if doc.created_at else "",
    }


@router.get("/files/{filename}")
async def serve_file(filename: str, current_user: dict = Depends(get_current_user)):
    safe_name = os.path.basename(filename)
    docs_dir = app_config.settings.documents_dir
    uploads_dir_config = app_config.settings.uploads_dir
    filepath = os.path.join(docs_dir, safe_name)
    if not os.path.exists(filepath):
        filepath = os.path.join(uploads_dir_config, safe_name)
    if not os.path.exists(filepath):
        raise HTTPException(404, "File not found")
    real = os.path.realpath(filepath)
    real_docs_dir = os.path.realpath(docs_dir)
    real_uploads_dir = os.path.realpath(uploads_dir_config)
    if not (real.startswith(real_docs_dir) or real.startswith(real_uploads_dir)):
        raise HTTPException(404, "File not found")
    return FileResponse(filepath, filename=safe_name)


# --- Tool info ---

@router.get("/tools")
async def list_tools(current_user: dict = Depends(get_current_user)):
    return skill_registry.get_tool_definitions()


app.include_router(router)


# Serve static frontend
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    # Serve PWA files as static files (must be before SPA catch-all)
    @app.get("/sw.js", include_in_schema=False)
    async def serve_sw():
        sw_path = os.path.join(static_dir, "sw.js")
        if os.path.exists(sw_path):
            return FileResponse(sw_path, media_type="application/javascript")
        raise HTTPException(404)

    @app.get("/manifest.json", include_in_schema=False)
    async def serve_manifest():
        manifest_path = os.path.join(static_dir, "manifest.json")
        if os.path.exists(manifest_path):
            return FileResponse(manifest_path, media_type="application/manifest+json")
        raise HTTPException(404)

    @app.get("/icons/{icon_path:path}", include_in_schema=False)
    async def serve_icon(icon_path: str):
        icon_full = os.path.join(static_dir, "icons", icon_path)
        if os.path.exists(icon_full) and os.path.isfile(icon_full):
            return FileResponse(icon_full)
        raise HTTPException(404)

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
