import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from . import config as app_config
from .database import init_db, get_db, ModelConfig, Conversation, Message, User, TokenUsageLog, SubscriptionPlan, PlanModelLimit, UserSubscription
from .models import (
    ModelConfigCreate, ModelConfigUpdate, ModelConfigResponse,
    ConversationCreate, ConversationResponse,
    ChatRequest, MessageResponse, BranchRequest,
    EnvUpdateRequest, EnvStatusResponse,
)
from .ai import get_provider, ToolDef
from .tools import get_tool_definitions, execute_tool
from .sandbox import is_docker_available
from .routers.auth import router as auth_router, get_current_user, require_role, load_provider_configs
from .routers.subscriptions import router as subscriptions_router

router = APIRouter(prefix="/api")


def get_active_provider_configs():
    return load_provider_configs()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="LLMDash", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(subscriptions_router)


# --- Model Config (admin only) ---

@router.get("/models", response_model=list[ModelConfigResponse])
async def list_models(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(ModelConfig).order_by(ModelConfig.id)
    if current_user.get("role") not in ("owner", "admin"):
        query = query.where(ModelConfig.enabled == True)
    result = await db.execute(query)
    models = result.scalars().all()
    return [
        ModelConfigResponse(
            id=m.id, name=m.name, provider=m.provider,
            model_name=m.model_name, base_url=m.base_url,
            api_key_env=m.api_key_env, temperature=m.temperature or 0.7,
            max_tokens=m.max_tokens or 4096,
            enabled=m.enabled,
            created_at=m.created_at.isoformat() if m.created_at else "",
            updated_at=m.updated_at.isoformat() if m.updated_at else "",
        )
        for m in models
    ]


@router.post("/models", status_code=201)
async def create_model(cfg: ModelConfigCreate, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    model = ModelConfig(
        name=cfg.name, provider=cfg.provider.value,
        model_name=cfg.model_name, base_url=cfg.base_url,
        api_key_env=cfg.api_key_env, temperature=cfg.temperature,
        max_tokens=cfg.max_tokens, enabled=cfg.enabled,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return {"id": model.id, "status": "created"}


@router.put("/models/{model_id}")
async def update_model(model_id: int, cfg: ModelConfigUpdate, current_user: dict = Depends(require_role("owner", "admin")), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ModelConfig).where(ModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(404, "Model not found")
    update_data = cfg.model_dump(exclude_unset=True)
    if "provider" in update_data and update_data["provider"] is not None:
        update_data["provider"] = update_data["provider"].value
    for key, value in update_data.items():
        setattr(model, key, value)
    model.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "updated"}


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
                            models.append({
                                "id": m.get("id", m.get("name", "")),
                                "name": m.get("id", m.get("name", "")),
                            })
                    else:
                        error = f"HTTP {resp.status_code}"
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
                            models.append({
                                "id": m.get("id", m.get("name", "")),
                                "name": m.get("display_name", m.get("id", "")),
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
        model_name=model.model_name, base_url=model.base_url,
        api_key_env=model.api_key_env, temperature=model.temperature or 0.7,
        max_tokens=model.max_tokens or 4096,
        enabled=model.enabled,
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
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
    )
    msgs = result.scalars().all()
    return [
        MessageResponse(
            id=m.id, role=m.role, content=m.content,
            tool_calls_json=json.loads(m.tool_calls_json) if m.tool_calls_json else None,
            tool_call_id=m.tool_call_id, tool_name=m.tool_name,
            reasoning_content=m.reasoning_content,
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
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.id)
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
    env_path = ".env"
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
    }


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

    provider = get_provider(model.provider)
    if not provider:
        raise HTTPException(400, f"Unknown provider: {model.provider}")

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

        if subscription_limit is None and free_plan and free_plan.token_limit is not None:
            subscription_limit = free_plan.token_limit

    if not active_sub_row:
        if free_plan and free_plan.token_limit is not None:
            subscription_limit = free_plan.token_limit

    if free_plan and model_subscription_limit is None:
        model_limit_result = await db.execute(
            select(PlanModelLimit).where(
                PlanModelLimit.plan_id == free_plan.id,
                PlanModelLimit.model_id == model_id,
            )
        )
        model_limit = model_limit_result.scalar_one_or_none()
        if model_limit and model_limit.token_limit is not None:
            model_subscription_limit = model_limit.token_limit

    effective_limit = None
    if user and user.token_limit is not None:
        effective_limit = user.token_limit
    if subscription_limit is not None:
        effective_limit = subscription_limit if effective_limit is None else min(effective_limit, subscription_limit)
    if model_subscription_limit is not None:
        effective_limit = model_subscription_limit if effective_limit is None else min(effective_limit, model_subscription_limit)

    if user and effective_limit is not None and (user.token_usage or 0) >= effective_limit:
        raise HTTPException(403, f"Token limit reached ({user.token_usage}/{effective_limit}). Upgrade your plan or contact an admin.")

    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == req.conversation_id).order_by(Message.id)
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
        "environment. Be thorough, accurate, and helpful."
    )
    messages.insert(0, {"role": "system", "content": system_prompt})

    user_msg = Message(conversation_id=req.conversation_id, role="user", content=req.message)
    db.add(user_msg)
    await db.commit()
    messages.append({"role": "user", "content": req.message})

    async def generate_title():
        try:
            title_msgs = [
                {"role": "system", "content": "Generate a very short, concise title (maximum 6 words) for a conversation that starts with this message. Return ONLY the title, no quotes or explanations."},
                {"role": "user", "content": req.message}
            ]
            result = await provider.chat(title_msgs, [], model)
            return result.content.strip()[:255] or "New Chat"
        except Exception:
            return "New Chat"

    title_task = asyncio.create_task(generate_title()) if not db_messages else None

    tool_defs = get_tool_definitions()
    tools = [ToolDef(**t) for t in tool_defs]

    async def event_stream():
        accumulated_content = ""
        accumulated_reasoning = ""
        final_tool_calls = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_total_tokens = 0

        try:
            async for chunk in provider.stream_chat(messages, tools, model):
                if chunk.content_delta:
                    accumulated_content += chunk.content_delta
                    yield f"data: {json.dumps({'type': 'content_delta', 'content': chunk.content_delta})}\n\n"
                if chunk.reasoning_content_delta:
                    accumulated_reasoning += chunk.reasoning_content_delta
                if chunk.tool_calls is not None:
                    final_tool_calls = chunk.tool_calls
                if chunk.usage:
                    total_prompt_tokens += chunk.usage["prompt_tokens"]
                    total_completion_tokens += chunk.usage["completion_tokens"]
                    total_total_tokens += chunk.usage["total_tokens"]

            tool_round = 0
            while final_tool_calls and tool_round < 5:
                tool_round += 1

                assistant_msg = Message(
                    conversation_id=req.conversation_id,
                    role="assistant",
                    content=accumulated_content or "",
                    tool_calls_json=json.dumps([
                        {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                        for tc in final_tool_calls
                    ]),
                    reasoning_content=accumulated_reasoning or None,
                )
                db.add(assistant_msg)
                await db.commit()

                yield f"data: {json.dumps({'type': 'tool_calls', 'tool_calls': final_tool_calls, 'content': accumulated_content})}\n\n"

                tool_results = []
                for tc in final_tool_calls:
                    yield f"data: {json.dumps({'type': 'tool_start', 'name': tc['name'], 'id': tc['id']})}\n\n"
                    result = await execute_tool(tc["name"], tc["arguments"])
                    tool_results.append({"tool_call_id": tc["id"], "content": result})
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': tc['name'], 'id': tc['id'], 'result': result})}\n\n"

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
                    )
                    db.add(tool_msg)
                    await db.commit()
                    messages.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "content": tr["content"]})

                accumulated_content = ""
                accumulated_reasoning = ""
                final_tool_calls = []

                next_tools = tools if tool_round == 1 else []
                async for chunk in provider.stream_chat_with_results(messages, next_tools, model, tool_results):
                    if chunk.content_delta:
                        accumulated_content += chunk.content_delta
                        yield f"data: {json.dumps({'type': 'content_delta', 'content': chunk.content_delta})}\n\n"
                    if chunk.reasoning_content_delta:
                        accumulated_reasoning += chunk.reasoning_content_delta
                    if chunk.tool_calls is not None:
                        final_tool_calls = chunk.tool_calls
                    if chunk.usage:
                        total_prompt_tokens += chunk.usage["prompt_tokens"]
                        total_completion_tokens += chunk.usage["completion_tokens"]
                        total_total_tokens += chunk.usage["total_tokens"]

            if not db_messages and title_task:
                try:
                    title = await title_task
                    conversation.title = title
                except Exception:
                    conversation.title = req.message[:80].replace("\n", " ") or "New Chat"
                await db.commit()

            if accumulated_content:

                assistant_msg = Message(
                    conversation_id=req.conversation_id,
                    role="assistant",
                    content=accumulated_content or "",
                )
                db.add(assistant_msg)
                await db.commit()

                yield f"data: {json.dumps({'type': 'content', 'content': accumulated_content})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            if total_total_tokens == 0:
                total_total_tokens = max(1, len(req.message) // 4)
                total_prompt_tokens = total_total_tokens
            try:
                usage_log = TokenUsageLog(
                    user_id=current_user["user_id"],
                    model_id=model_id,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_total_tokens,
                )
                db.add(usage_log)
                if user:
                    await db.execute(
                        update(User)
                        .where(User.id == user.id)
                        .values(token_usage=User.token_usage + total_total_tokens)
                    )
                await db.commit()
            except Exception as e:
                print(f"Token usage recording failed: {e}", flush=True)
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- File Serving ---

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
