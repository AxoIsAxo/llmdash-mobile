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
    token_usage_by_model: dict = {}
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
    vision_enabled: bool = False
    audio_enabled: bool = False
    tools_enabled: bool = True
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
    vision_enabled: Optional[bool] = None
    audio_enabled: Optional[bool] = None
    tools_enabled: Optional[bool] = None
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
    vision_enabled: bool = False
    audio_enabled: bool = False
    tools_enabled: bool = True
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
    message: str = ""
    model_id: Optional[int] = None
    attachments: Optional[list[dict]] = None


class UploadResponse(BaseModel):
    filename: str
    file_path: str
    file_type: str
    file_size: int
    ocr_text: Optional[str] = None


class FileUploadSettings(BaseModel):
    file_upload_enabled: bool = True
    ocr_enabled: bool = True
    ocr_strategy: str = "ocr"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    whisper_device: str = "auto"
    whisper_language: Optional[str] = None
    whisper_beam_size: int = 1
    whisper_provider: str = "local"
    whisper_openrouter_model: str = "openai/whisper-1"


class FileUploadSettingsUpdate(BaseModel):
    file_upload_enabled: Optional[bool] = None
    ocr_enabled: Optional[bool] = None
    ocr_strategy: Optional[str] = None
    whisper_model: Optional[str] = None
    whisper_compute_type: Optional[str] = None
    whisper_device: Optional[str] = None
    whisper_language: Optional[str] = None
    whisper_beam_size: Optional[int] = None
    whisper_provider: Optional[str] = None
    whisper_openrouter_model: Optional[str] = None


class ImageGenerationRequest(BaseModel):
    conversation_id: int
    prompt: str = Field(..., min_length=1, max_length=4000)
    model_id: int
    size: Optional[str] = "1024x1024"
    n: Optional[int] = 1


class BranchRequest(BaseModel):
    message_index: int = Field(..., ge=0)


class MessageResponse(BaseModel):
    id: int
    role: str
    content: Optional[str] = None
    attachments_json: Optional[Any] = None
    tool_calls_json: Optional[Any] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    reasoning_content: Optional[str] = None
    thinking_json: Optional[Any] = None
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


class CssUpdateRequest(BaseModel):
    css: str


class AutoScrollUpdateRequest(BaseModel):
    auto_scroll: bool


class TtsRequest(BaseModel):
    # P7 text-to-speech: the text to speak + an optional provider voice id.
    text: str = Field(..., min_length=1, max_length=4000)
    voice: Optional[str] = Field(None, max_length=128)


class SubscriptionPlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    price_sats: int = Field(..., ge=0)
    duration_days: int = Field(..., ge=0)
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    enabled: bool = True
    # P9: optional per-feature entitlement overrides (defaults all-on).
    entitlements: Optional[dict[str, bool]] = None


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    price_sats: Optional[int] = Field(None, ge=0)
    duration_days: Optional[int] = Field(None, ge=0)
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    enabled: Optional[bool] = None
    entitlements: Optional[dict[str, bool]] = None


class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    price_sats: int
    duration_days: int
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    enabled: bool
    created_at: str
    # P9: full merged entitlement dict (defaults + plan overrides).
    entitlements: dict[str, bool] = {}


class PlanModelLimitResponse(BaseModel):
    id: int
    plan_id: int
    model_id: int
    model_name: str
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    allowed: bool = True


class PlanModelLimitSet(BaseModel):
    model_id: int
    token_limit: Optional[int] = None
    image_limit: Optional[int] = None
    allowed: bool = True


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
