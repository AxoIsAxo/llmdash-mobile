export interface User {
  id: number;
  username: string;
  role: 'owner' | 'admin' | 'user';
  token_limit: number | null;
  token_usage: number;
  image_limit: number | null;
  image_usage: number;
  created_at: string;
}

export interface AuthStatus {
  needs_setup: boolean;
  registration_enabled: boolean;
}

export interface AuthResponse {
  token: string;
  user: User;
}

export interface ModelConfig {
  id: number;
  name: string;
  provider: 'openai_compatible' | 'anthropic';
  model_name: string;
  model_type: string;
  base_url: string | null;
  api_key_env: string | null;
  temperature: number;
  max_tokens: number;
  thinking_enabled: boolean;
  thinking_budget_tokens: number | null;
  vision_enabled: boolean;
  enabled: boolean;
  sort_order: number | null;
  created_at: string;
  updated_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  model_id: number | null;
  user_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string | null;
  attachments_json?: AttachmentRecord[] | null;
  tool_calls_json: ToolCall[] | null;
  tool_call_id: string | null;
  tool_name: string | null;
  reasoning_content?: string | null;
  status?: string;
  created_at: string;
}

export interface GenerateStatus {
  generating: boolean;
  message_id: number | null;
  message_content: string | null;
  tool_calls_json: ToolCall[] | null;
  reasoning_content: string | null;
  status: string;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface StreamEvent {
  type: 'content' | 'content_delta' | 'reasoning_delta' | 'tool_calls' | 'tool_start' | 'tool_result' | 'error' | 'image_result';
  content?: string;
  reasoning_content_delta?: string;
  reasoning_content?: string;
  tool_calls?: ToolCall[];
  name?: string;
  id?: string;
  result?: string;
  error?: string;
  images?: string[];
  revised_prompt?: string;
  prompt?: string;
  size?: string;
}

export interface ConfigStatus {
  docker_available: boolean;
  tools: Record<string, boolean>;
  providers: Record<string, boolean>;
}

export interface EnvStatus {
  configured: string[];
  available: string[];
}

export interface ScannedModel {
  id: string;
  name: string;
  suggested_type: string;
}

export interface ScannedProvider {
  provider_key: string;
  provider_name: string;
  provider_type: string;
  base_url: string;
  env_var: string;
  models: ScannedModel[];
  error: string | null;
}

export interface ProviderConfig {
  key: string;
  name: string;
  type: string;
  env_var: string;
  base_url: string;
}

export interface RegistrationStatus {
  enabled: boolean;
}

export interface IpLimitStatus {
  limit: number;
}

export interface SubscriptionPlan {
  id: number;
  name: string;
  price_sats: number;
  duration_days: number;
  token_limit: number | null;
  image_limit: number | null;
  enabled: boolean;
  created_at: string;
}

export interface PlanModelLimit {
  id: number;
  plan_id: number;
  model_id: number;
  model_name: string;
  token_limit: number | null;
  image_limit: number | null;
}

export interface UserSubscription {
  id: number;
  user_id: number;
  plan_id: number | null;
  plan_name: string | null;
  plan_token_limit: number | null;
  plan_image_limit: number | null;
  status: 'pending' | 'active' | 'expired' | 'cancelled';
  started_at: string | null;
  expires_at: string | null;
  payment_checking_id: string | null;
  payment_request: string | null;
  token_usage: number;
  image_usage: number;
  created_at: string;
}

export interface SubscribeResult {
  subscription_id: number;
  status: string;
  payment_request?: string;
  payment_hash?: string;
  checking_id?: string;
  plan_name?: string;
}

export interface AttachmentRecord {
  filename: string;
  file_type: string;
  file_path: string;
  ocr_text?: string | null;
  image_included?: boolean;
}

export interface UploadResponse {
  filename: string;
  file_path: string;
  file_type: string;
  file_size: number;
  ocr_text: string | null;
}

export interface FileUploadSettings {
  file_upload_enabled: boolean;
  ocr_enabled: boolean;
  ocr_strategy: string;
}
