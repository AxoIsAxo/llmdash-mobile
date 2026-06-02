import hashlib
import secrets
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, JSON
from datetime import datetime, timezone
import os

from . import config as app_config


os.makedirs(os.path.dirname(app_config.settings.database_path) or ".", exist_ok=True)
db_url = f"sqlite+aiosqlite:///{app_config.settings.database_path}"
engine = create_async_engine(db_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default="user")
    token_limit = Column(Integer, nullable=True, default=None)
    token_usage = Column(Integer, nullable=False, default=0)
    image_limit = Column(Integer, nullable=True, default=None)
    image_usage = Column(Integer, nullable=False, default=0)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        return f"{salt}:{h}"

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            salt, h = password_hash.split(":", 1)
            return h == hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
        except (ValueError, AttributeError):
            return False


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    provider = Column(String(64), nullable=False)
    model_name = Column(String(255), nullable=False)
    model_type = Column(String(16), nullable=False, default="chat")
    base_url = Column(String(512), nullable=True)
    api_key_env = Column(String(128), nullable=True)
    temperature = Column(Float, default=0.7)
    max_tokens = Column(Integer, default=4096)
    thinking_enabled = Column(Boolean, default=False)
    thinking_budget_tokens = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), default="New Chat")
    model_id = Column(Integer, ForeignKey("model_configs.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    container_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=True)
    tool_calls_json = Column(Text, nullable=True)
    tool_call_id = Column(String(128), nullable=True)
    tool_name = Column(String(128), nullable=True)
    reasoning_content = Column(Text, nullable=True)
    status = Column(String(32), default="done", nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TokenUsageLog(Base):
    __tablename__ = "token_usage_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    model_id = Column(Integer, ForeignKey("model_configs.id", ondelete="SET NULL"), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    reasoning_tokens = Column(Integer, nullable=True, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    price_sats = Column(Integer, nullable=False, default=0)
    duration_days = Column(Integer, nullable=False, default=30)
    token_limit = Column(Integer, nullable=True)
    image_limit = Column(Integer, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PlanModelLimit(Base):
    __tablename__ = "plan_model_limits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="CASCADE"), nullable=False)
    model_id = Column(Integer, ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False)
    token_limit = Column(Integer, nullable=True)
    image_limit = Column(Integer, nullable=True)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    payment_checking_id = Column(String(128), nullable=True)
    payment_hash = Column(String(128), nullable=True)
    payment_request = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate)
        await conn.run_sync(_seed_default_plan)


def _seed_default_plan(conn):
    existing = conn.exec_driver_sql(
        "SELECT id FROM subscription_plans WHERE name = 'Free'"
    ).fetchone()
    if not existing:
        conn.exec_driver_sql(
            "INSERT INTO subscription_plans (name, price_sats, duration_days, token_limit, enabled) "
            "VALUES ('Free', 0, 0, NULL, 1)"
        )


def _migrate(conn):
    result = conn.exec_driver_sql("PRAGMA table_info(users)")
    existing = {row[1] for row in result}
    if "token_limit" not in existing:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN token_limit INTEGER")
    if "token_usage" not in existing:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN token_usage INTEGER NOT NULL DEFAULT 0")
    if "ip_address" not in existing:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN ip_address TEXT")
    if "image_limit" not in existing:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN image_limit INTEGER")
    if "image_usage" not in existing:
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN image_usage INTEGER NOT NULL DEFAULT 0")

    msg_result = conn.exec_driver_sql("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in msg_result}
    if "status" not in msg_cols:
        conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'done'")

    model_cfg_result = conn.exec_driver_sql("PRAGMA table_info(model_configs)")
    model_cfg_cols = {row[1] for row in model_cfg_result}
    if "thinking_enabled" not in model_cfg_cols:
        conn.exec_driver_sql("ALTER TABLE model_configs ADD COLUMN thinking_enabled BOOLEAN DEFAULT 0")
    if "thinking_budget_tokens" not in model_cfg_cols:
        conn.exec_driver_sql("ALTER TABLE model_configs ADD COLUMN thinking_budget_tokens INTEGER")
    if "sort_order" not in model_cfg_cols:
        conn.exec_driver_sql("ALTER TABLE model_configs ADD COLUMN sort_order INTEGER")
    if "model_type" not in model_cfg_cols:
        conn.exec_driver_sql("ALTER TABLE model_configs ADD COLUMN model_type VARCHAR(16) NOT NULL DEFAULT 'chat'")

    token_result = conn.exec_driver_sql("PRAGMA table_info(token_usage_log)")
    token_cols = {row[1] for row in token_result}
    if "reasoning_tokens" not in token_cols:
        conn.exec_driver_sql("ALTER TABLE token_usage_log ADD COLUMN reasoning_tokens INTEGER DEFAULT 0")

    conv_result = conn.exec_driver_sql("PRAGMA table_info(conversations)")
    conv_cols = {row[1] for row in conv_result}
    if "container_id" not in conv_cols:
        conn.exec_driver_sql("ALTER TABLE conversations ADD COLUMN container_id VARCHAR(128)")

    try:
        existing_tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "subscription_plans" not in existing_tables:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS subscription_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(64) UNIQUE NOT NULL,
                    price_sats INTEGER NOT NULL DEFAULT 0,
                    duration_days INTEGER NOT NULL DEFAULT 30,
                    token_limit INTEGER,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at DATETIME
                )
            """)
        if "plan_model_limits" not in existing_tables:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS plan_model_limits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL REFERENCES subscription_plans(id) ON DELETE CASCADE,
                    model_id INTEGER NOT NULL REFERENCES model_configs(id) ON DELETE CASCADE,
                    token_limit INTEGER
                )
            """)
        if "user_subscriptions" not in existing_tables:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan_id INTEGER REFERENCES subscription_plans(id) ON DELETE SET NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    started_at DATETIME,
                    expires_at DATETIME,
                    payment_checking_id VARCHAR(128),
                    payment_hash VARCHAR(128),
                    payment_request TEXT,
                    created_at DATETIME
                )
            """)
        else:
            sub_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user_subscriptions)").fetchall()}
            if "payment_request" not in sub_cols:
                conn.exec_driver_sql("ALTER TABLE user_subscriptions ADD COLUMN payment_request TEXT")

        sub_plan_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(subscription_plans)").fetchall()}
        if "image_limit" not in sub_plan_cols:
            conn.exec_driver_sql("ALTER TABLE subscription_plans ADD COLUMN image_limit INTEGER")

        plan_limit_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(plan_model_limits)").fetchall()}
        if "image_limit" not in plan_limit_cols:
            conn.exec_driver_sql("ALTER TABLE plan_model_limits ADD COLUMN image_limit INTEGER")
    except Exception:
        pass


async def get_db():
    async with async_session() as session:
        yield session
