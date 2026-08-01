from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import config as app_config
from ..database import (
    get_db, SubscriptionPlan, PlanModelLimit, UserSubscription,
    User, ModelConfig,
)
from ..models import (
    SubscriptionPlanCreate, SubscriptionPlanUpdate, SubscriptionPlanResponse,
    PlanModelLimitResponse, PlanModelLimitSet,
    UserSubscriptionResponse, SubscribeRequest,
)
from .auth import get_current_user, require_role

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])

LNBITS_TIMEOUT = 15


def lnbits_url(path: str) -> Optional[str]:
    base = app_config.settings.lnbits_url
    if not base:
        return None
    return f"{base.rstrip('/')}{path}"


def lnbits_headers() -> dict:
    key = app_config.settings.lnbits_invoice_key
    return {"X-Api-Key": key} if key else {}


async def check_lnbits_configured():
    if not app_config.settings.lnbits_url or not app_config.settings.lnbits_invoice_key:
        raise HTTPException(400, "LNBits is not configured. Set LNBITS_URL and LNBITS_INVOICE_KEY in .env")


# --- Plans (admin) ---

@router.get("/plans", response_model=list[SubscriptionPlanResponse])
async def list_plans(current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SubscriptionPlan).order_by(SubscriptionPlan.id))
    plans = result.scalars().all()
    return [
        SubscriptionPlanResponse(
            id=p.id, name=p.name, price_sats=p.price_sats,
            duration_days=p.duration_days, token_limit=p.token_limit,
            image_limit=getattr(p, "image_limit", None),
            enabled=p.enabled,
            created_at=p.created_at.isoformat() if p.created_at else "",
        )
        for p in plans
    ]


@router.get("/plans/public", response_model=list[SubscriptionPlanResponse])
async def list_public_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.enabled == True).order_by(SubscriptionPlan.id)
    )
    plans = result.scalars().all()
    return [
        SubscriptionPlanResponse(
            id=p.id, name=p.name, price_sats=p.price_sats,
            duration_days=p.duration_days, token_limit=p.token_limit,
            image_limit=getattr(p, "image_limit", None),
            enabled=p.enabled,
            created_at=p.created_at.isoformat() if p.created_at else "",
        )
        for p in plans
    ]


@router.post("/plans", status_code=201)
async def create_plan(
    req: SubscriptionPlanCreate,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.name == req.name))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Plan name already exists")
    plan = SubscriptionPlan(
        name=req.name, price_sats=req.price_sats,
        duration_days=req.duration_days, token_limit=req.token_limit,
        image_limit=getattr(req, "image_limit", None),
        enabled=req.enabled,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return {"id": plan.id, "status": "created"}


@router.put("/plans/{plan_id}")
async def update_plan(
    plan_id: int,
    req: SubscriptionPlanUpdate,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found")

    update_data = req.model_dump(exclude_unset=True)
    if "name" in update_data and update_data["name"] != plan.name:
        existing = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.name == update_data["name"], SubscriptionPlan.id != plan_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Plan name already exists")

    for key, value in update_data.items():
        setattr(plan, key, value)
    await db.commit()
    return {"status": "updated"}


@router.delete("/plans/{plan_id}")
async def delete_plan(
    plan_id: int,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan.name == "Free":
        raise HTTPException(400, "Cannot delete the Free plan")
    await db.delete(plan)
    await db.commit()
    return {"status": "deleted"}


# --- Model limits (admin) ---

@router.get("/plans/{plan_id}/limits", response_model=list[PlanModelLimitResponse])
async def list_plan_limits(
    plan_id: int,
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlanModelLimit, ModelConfig.name).join(ModelConfig, PlanModelLimit.model_id == ModelConfig.id, isouter=True)
        .where(PlanModelLimit.plan_id == plan_id)
    )
    rows = result.all()
    return [
        PlanModelLimitResponse(
            id=limit.id, plan_id=limit.plan_id, model_id=limit.model_id,
            model_name=model_name or f"Model #{limit.model_id}",
            token_limit=limit.token_limit,
            image_limit=getattr(limit, "image_limit", None),
        )
        for limit, model_name in rows
    ]


@router.put("/plans/{plan_id}/limits")
async def set_plan_limits(
    plan_id: int,
    req: list[PlanModelLimitSet],
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    plan_result = await db.execute(select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id))
    if not plan_result.scalar_one_or_none():
        raise HTTPException(404, "Plan not found")

    from sqlalchemy import delete
    await db.execute(delete(PlanModelLimit).where(PlanModelLimit.plan_id == plan_id))

    for item in req:
        limit = PlanModelLimit(plan_id=plan_id, model_id=item.model_id, token_limit=item.token_limit, image_limit=getattr(item, "image_limit", None))
        db.add(limit)

    await db.commit()
    return {"status": "updated"}


# --- User subscription ---

@router.get("/my", response_model=Optional[UserSubscriptionResponse])
async def get_my_subscription(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserSubscription, SubscriptionPlan.name, User.token_usage, User.image_usage)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id, isouter=True)
        .join(User, UserSubscription.user_id == User.id)
        .where(UserSubscription.user_id == current_user["user_id"])
        .order_by(UserSubscription.created_at.desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return None

    sub, plan_name, token_usage, image_usage = row
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if sub.status == "active" and sub.expires_at and sub.expires_at.replace(tzinfo=None) < now:
        sub.status = "expired"
        await db.commit()

    if sub.status in ("expired", "cancelled"):
        return None

    if sub.status == "pending" and sub.payment_checking_id and not sub.payment_request:
        try:
            async with httpx.AsyncClient(timeout=LNBITS_TIMEOUT) as client:
                resp = await client.get(
                    lnbits_url(f"/api/v1/payments/{sub.payment_checking_id}"),
                    headers=lnbits_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    pr = data.get("payment_request") or data.get("bolt11") or data.get("invoice")
                    if pr:
                        sub.payment_request = str(pr)
                        await db.commit()
        except Exception:
            pass

    plan_token_limit = None
    plan_image_limit = None
    if sub.plan_id:
        plan_result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == sub.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan:
            plan_token_limit = plan.token_limit
            plan_image_limit = getattr(plan, "image_limit", None)

    if plan_token_limit is None:
        free_plan_result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.name == "Free")
        )
        free_plan = free_plan_result.scalar_one_or_none()
        if free_plan and free_plan.token_limit is not None:
            plan_token_limit = free_plan.token_limit

    return UserSubscriptionResponse(
        id=sub.id, user_id=sub.user_id, plan_id=sub.plan_id,
        plan_name=plan_name, plan_token_limit=plan_token_limit,
        plan_image_limit=plan_image_limit, status=sub.status,
        started_at=sub.started_at.isoformat() if sub.started_at else None,
        expires_at=sub.expires_at.isoformat() if sub.expires_at else None,
        payment_checking_id=sub.payment_checking_id,
        payment_request=sub.payment_request,
        token_usage=token_usage or 0,
        image_usage=image_usage or 0,
        created_at=sub.created_at.isoformat() if sub.created_at else "",
    )


@router.post("/subscribe", status_code=201)
async def subscribe(
    req: SubscribeRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan_result = await db.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == req.plan_id, SubscriptionPlan.enabled == True)
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "Plan not found or disabled")

    existing_active = await db.execute(
        select(UserSubscription).where(
            UserSubscription.user_id == current_user["user_id"],
            UserSubscription.plan_id == plan.id,
            UserSubscription.status.in_(("active", "pending")),
        )
    )
    if existing_active.scalar_one_or_none():
        raise HTTPException(409, f"You already have an active or pending subscription to the {plan.name} plan")

    if plan.name == "Free" or plan.price_sats == 0:
        now = datetime.now(timezone.utc)
        sub = UserSubscription(
            user_id=current_user["user_id"],
            plan_id=plan.id,
            status="active",
            started_at=now,
            expires_at=now + timedelta(days=plan.duration_days) if plan.duration_days > 0 else None,
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)
        return {"subscription_id": sub.id, "status": "active", "plan_name": plan.name}

    await check_lnbits_configured()

    memo = f"LLMDash: {plan.name} plan ({plan.duration_days} days)"
    payload = {
        "out": False,
        "amount": plan.price_sats,
        "memo": memo,
        "expiry": 86400,
        "extra": {
            "tag": "llmdash_subscription",
            "user_id": current_user["user_id"],
            "plan_id": plan.id,
        },
    }

    try:
        url = lnbits_url("/api/v1/payments")
        async with httpx.AsyncClient(timeout=LNBITS_TIMEOUT) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=lnbits_headers(),
            )
            if resp.status_code not in (200, 201):
                raise HTTPException(502, f"LNBits error ({resp.status_code}): {resp.text}")
            data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LNBits connection error: {str(e)}")

    sub = UserSubscription(
        user_id=current_user["user_id"],
        plan_id=plan.id,
        status="pending",
        payment_checking_id=data.get("checking_id"),
        payment_hash=data.get("payment_hash"),
        payment_request=data.get("payment_request"),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    return {
        "subscription_id": sub.id,
        "status": "pending",
        "payment_request": data.get("payment_request"),
        "payment_hash": data.get("payment_hash"),
        "checking_id": data.get("checking_id"),
        "plan_name": plan.name,
    }


@router.post("/check-payment/{subscription_id}")
async def check_payment(
    subscription_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_lnbits_configured()

    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.id == subscription_id,
            UserSubscription.user_id == current_user["user_id"],
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subscription not found")

    if sub.status == "active":
        return {"status": "active"}

    if not sub.payment_checking_id:
        raise HTTPException(400, "No payment to check")

    try:
        async with httpx.AsyncClient(timeout=LNBITS_TIMEOUT) as client:
            resp = await client.get(
                lnbits_url(f"/api/v1/payments/{sub.payment_checking_id}"),
                headers=lnbits_headers(),
            )
            if resp.status_code != 200:
                raise HTTPException(502, f"LNBits error: {resp.text}")
            data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"LNBits connection error: {str(e)}")

    if data.get("paid"):
        plan_result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == sub.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        duration = plan.duration_days if plan else 30
        sub.status = "active"
        sub.started_at = now
        sub.expires_at = now + timedelta(days=duration) if duration > 0 else None
        await db.commit()
        return {"status": "active", "plan_name": plan.name if plan else ""}

    return {"status": "pending"}


# --- Webhook for LNBits payment notifications ---

@router.post("/webhook")
async def payment_webhook(request: dict, db: AsyncSession = Depends(get_db)):
    """Payment notification endpoint.

    This endpoint is intentionally NOT trusted as a payment proof: the
    checking_id is client-visible (it is returned by /subscribe), so anyone
    could POST it here. Before activating a subscription we ALWAYS re-verify
    the payment state with LNBits itself (paid == true). A payment can never
    be activated by this webhook alone.
    """
    payment_checking_id = request.get("checking_id") or request.get("payment_hash")
    if not payment_checking_id:
        raise HTTPException(400, "Missing payment identifier")

    result = await db.execute(
        select(UserSubscription).where(
            (UserSubscription.payment_checking_id == payment_checking_id)
            | (UserSubscription.payment_hash == payment_checking_id)
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subscription not found")

    if sub.status == "pending" and sub.payment_checking_id:
        try:
            async with httpx.AsyncClient(timeout=LNBITS_TIMEOUT) as client:
                resp = await client.get(
                    lnbits_url(f"/api/v1/payments/{sub.payment_checking_id}"),
                    headers=lnbits_headers(),
                )
                if resp.status_code != 200:
                    raise HTTPException(502, f"LNBits error: {resp.text}")
                data = resp.json()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"LNBits connection error: {str(e)}")

        # Only activate if LNBits confirms the invoice was actually paid.
        if not data.get("paid"):
            return {"status": "ok", "verified": False}

        plan_result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == sub.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        duration = plan.duration_days if plan else 30
        sub.status = "active"
        sub.started_at = now
        sub.expires_at = now + timedelta(days=duration) if duration > 0 else None
        await db.commit()

    return {"status": "ok", "verified": True}


# --- Admin: list all subscriptions ---

@router.get("/admin/all", response_model=list[UserSubscriptionResponse])
async def admin_list_subscriptions(
    user_id: Optional[int] = Query(None),
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(UserSubscription, SubscriptionPlan.name, User.username, User.token_usage, User.image_usage)
        .join(SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id, isouter=True)
        .join(User, UserSubscription.user_id == User.id)
    )
    if user_id is not None:
        query = query.where(UserSubscription.user_id == user_id)
    query = query.order_by(UserSubscription.created_at.desc()).limit(100)

    result = await db.execute(query)
    rows = result.all()
    return [
        UserSubscriptionResponse(
            id=sub.id, user_id=sub.user_id, plan_id=sub.plan_id,
            plan_name=plan_name, status=sub.status,
            started_at=sub.started_at.isoformat() if sub.started_at else None,
            expires_at=sub.expires_at.isoformat() if sub.expires_at else None,
            payment_checking_id=sub.payment_checking_id,
            payment_request=sub.payment_request,
            token_usage=token_usage or 0,
            image_usage=image_usage or 0,
            created_at=sub.created_at.isoformat() if sub.created_at else "",
        )
        for sub, plan_name, _username, token_usage, image_usage in rows
    ]


# --- User: cancel own subscription ---

@router.post("/cancel/{subscription_id}")
async def cancel_subscription(
    subscription_id: int,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.id == subscription_id,
            UserSubscription.user_id == current_user["user_id"],
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subscription not found")

    if sub.status not in ("active", "pending"):
        raise HTTPException(400, "Only active or pending subscriptions can be cancelled")

    sub.status = "cancelled"
    await db.commit()
    return {"status": "cancelled"}


# --- Admin: manually activate/cancel subscription ---

@router.put("/admin/{subscription_id}")
async def admin_update_subscription(
    subscription_id: int,
    status: str = Query(..., pattern="^(active|expired|cancelled)$"),
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(UserSubscription).where(UserSubscription.id == subscription_id))
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subscription not found")

    sub.status = status
    if status == "active":
        sub.started_at = datetime.now(timezone.utc)
        sub.expires_at = None
        plan_result = await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == sub.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan and plan.duration_days > 0:
            sub.expires_at = sub.started_at + timedelta(days=plan.duration_days)
    await db.commit()
    return {"status": "updated"}


@router.delete("/admin/expired")
async def clear_expired_subscriptions(
    current_user: dict = Depends(require_role("owner", "admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        delete(UserSubscription).where(UserSubscription.status == "expired")
    )
    await db.commit()
    return {"deleted": result.rowcount}
