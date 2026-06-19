from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import User, UserModelUsage, SubscriptionPlan, PlanModelLimit


async def check_token_limits(
    db: AsyncSession,
    user_id: int,
    model_id: int,
    user: Optional[User],
    subscription_result,
    free_plan_result,
):
    subscription_limit: Optional[int] = None
    model_subscription_limit: Optional[int] = None

    active_sub_row = subscription_result.first()
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

    if not active_sub_row and free_plan and free_plan.token_limit is not None:
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

    global_limit: Optional[int] = None
    if user and user.token_limit is not None:
        global_limit = user.token_limit
    if subscription_limit is not None:
        global_limit = subscription_limit if global_limit is None else min(global_limit, subscription_limit)

    if user and model_subscription_limit is not None:
        per_model_result = await db.execute(
            select(UserModelUsage).where(
                UserModelUsage.user_id == user_id,
                UserModelUsage.model_id == model_id,
            )
        )
        per_model_row = per_model_result.scalar_one_or_none()
        per_model_usage = per_model_row.token_usage if per_model_row else 0
        if per_model_usage >= model_subscription_limit:
            raise HTTPException(403, f"Token limit reached for this model ({per_model_usage}/{model_subscription_limit}). Upgrade your plan or contact an admin.")

    if user and global_limit is not None and (user.token_usage or 0) >= global_limit:
        raise HTTPException(403, f"Token limit reached ({user.token_usage}/{global_limit}). Upgrade your plan or contact an admin.")
