from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum


class ProviderType(str, Enum):
    openai_compatible = "openai_compatible"
    anthropic = "anthropic"


class ModelType(str, Enum):
    chat = "chat"
    image = "image"


class UserRole(str, Enum):
    owner = "owner"
    admin = "admin"
    user = "user"


class AuthSetupRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)


class AuthLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AuthRegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    token_limit: Optional[int] = None
    token_usage: int = 0
    image_limit: Optional[int] = None
    image_usage: int = 0
    created_at: str


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=1, max_length=64)
    password: Optional[str] = Field(None, min_length=4, max_length=128)
    role: Optional[UserRole] = None
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None


class IpLimitResponse(BaseModel):
    limit: int


class IpLimitUpdateRequest(BaseModel):
    limit: int = Field(..., ge=1, le=100)


class RegistrationToggleRequest(BaseModel):
    enabled: bool


class ProviderConfig(BaseModel):
    key: str
    name: str
    type: str
    env_var: str
    base_url: str


class ProviderConfigUpdate(BaseModel):
    key: str
    name: Optional[str] = None
    base_url: Optional[str] = None


class ModelConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: ProviderType
    model_name: str = Field(..., min_length=1)
    model_type: ModelType = ModelType.chat
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    thinking_enabled: bool = False
    thinking_budget_tokens: Optional[int] = None
    enabled: bool = True
    sort_order: Optional[int] = None


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[ProviderType] = None
    model_name: Optional[str] = None
    model_type: Optional[ModelType] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking_enabled: Optional[bool] = None
    thinking_budget_tokens: Optional[int] = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class ModelConfigResponse(BaseModel):
    id: int
    name: str
    provider: str
    model_name: str
    model_type: str = "chat"
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    thinking_enabled: bool = False
    thinking_budget_tokens: Optional[int] = None
    enabled: bool
    sort_order: Optional[int] = None
    created_at: str
    updated_at: str


class ModelReorderRequest(BaseModel):
    model_ids: list[int]


class ConversationCreate(BaseModel):
    title: Optional[str] = "New Chat"
    model_id: Optional[int] = None


class ConversationResponse(BaseModel):
    id: int
    title: str
    model_id: Optional[int] = None
    user_id: Optional[int] = None
    created_at: str
    updated_at: str


class ChatRequest(BaseModel):
    conversation_id: int
    message: str = Field(..., min_length=1)
    model_id: Optional[int] = None


class ImageGenerationRequest(BaseModel):
    conversation_id: int
    prompt: str = Field(..., min_length=1, max_length=4000)
    model_id: int
    size: Optional[str] = "1024x1024"
    n: Optional[int] = 1


class BranchRequest(BaseModel):
    message_index: int = Field(..., ge=0)


class ToolCallSchema(BaseModel):
    id: str
    name: str
    arguments: dict


class ToolResultMessage(BaseModel):
    role: str = "tool"
    tool_call_id: str
    content: str


class MessageResponse(BaseModel):
    id: int
    role: str
    content: Optional[str] = None
    tool_calls_json: Optional[Any] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    reasoning_content: Optional[str] = None
    status: str = "done"
    created_at: str

class GenerateStatusResponse(BaseModel):
    generating: bool
    message_id: Optional[int] = None
    message_content: Optional[str] = None
    tool_calls_json: Optional[Any] = None
    reasoning_content: Optional[str] = None
    status: str = "done"


class ImageGenerationResponse(BaseModel):
    images: list[str]
    revised_prompt: Optional[str] = None


class EnvUpdateRequest(BaseModel):
    updates: dict[str, str]


class EnvStatusResponse(BaseModel):
    configured: list[str]
    available: list[str]


class ChatStreamEvent(BaseModel):
    type: str
    data: Any


class SubscriptionPlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    price_sats: int = Field(..., ge=0)
    duration_days: int = Field(..., ge=0)
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    enabled: bool = True


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    price_sats: Optional[int] = Field(None, ge=0)
    duration_days: Optional[int] = Field(None, ge=0)
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    enabled: Optional[bool] = None


class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    price_sats: int
    duration_days: int
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    enabled: bool
    created_at: str


class PlanModelLimitResponse(BaseModel):
    id: int
    plan_id: int
    model_id: int
    model_name: str
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None


class PlanModelLimitSet(BaseModel):
    model_id: int
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None


class UserSubscriptionResponse(BaseModel):
    id: int
    user_id: int
    plan_id: Optional[int] = None
    plan_name: Optional[str] = None
    plan_token_limit: Optional[int] = None
    plan_image_limit: Optional[int] = None
    status: str
    started_at: Optional[str] = None
    expires_at: Optional[str] = None
    payment_checking_id: Optional[str] = None
    payment_request: Optional[str] = None
    token_usage: int = 0
    image_usage: int = 0
    created_at: str


class SubscribeRequest(BaseModel):
    plan_id: int
