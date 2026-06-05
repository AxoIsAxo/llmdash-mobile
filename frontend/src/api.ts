const BASE = '/api';

function getToken(): string | null {
  return localStorage.getItem('llmdash_token');
}

function setToken(token: string | null) {
  if (token) localStorage.setItem('llmdash_token', token);
  else localStorage.removeItem('llmdash_token');
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, {
    headers,
    ...opts,
  });
  if (res.status === 401) {
    setToken(null);
    window.location.reload();
    throw new Error('Unauthorized');
  }
  if (!res.ok) {
    let text = '';
    try { text = await res.text() } catch {}
    throw new Error(`${res.status}: ${text || 'Unknown error'}`);
  }
  return res.json();
}

export const api = {
  getToken,
  setToken,

  auth: {
    status: () =>
      fetch(`${BASE}/auth/status`).then(r => r.json()) as Promise<import('./types').AuthStatus>,

    setup: (username: string, password: string) =>
      fetch(`${BASE}/auth/setup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t) });
        return r.json();
      }) as Promise<import('./types').AuthResponse>,

    login: (username: string, password: string) =>
      fetch(`${BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }).then(r => {
        if (!r.ok) {
          if (r.status === 401) throw new Error('Invalid username or password');
          return r.text().then(t => { throw new Error(t) });
        }
        return r.json();
      }) as Promise<import('./types').AuthResponse>,

    register: (username: string, password: string) =>
      fetch(`${BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }).then(r => {
        if (!r.ok) return r.text().then(t => { throw new Error(t) });
        return r.json();
      }) as Promise<import('./types').AuthResponse>,

    me: () => request<import('./types').User>('/auth/me'),

    users: {
      list: () => request<import('./types').User[]>('/auth/users'),
      create: (username: string, password: string) =>
        request<import('./types').User>('/auth/users', {
          method: 'POST',
          body: JSON.stringify({ username, password }),
        }),
      update: (id: number, data: { username?: string; password?: string; role?: string; token_limit?: number | null; image_limit?: number | null }) =>
        request<{ status: string }>(`/auth/users/${id}`, {
          method: 'PUT',
          body: JSON.stringify(data),
        }),
      delete: (id: number) =>
        request<{ status: string }>(`/auth/users/${id}`, { method: 'DELETE' }),
      resetUsage: (id: number) =>
        request<{ status: string }>(`/auth/users/${id}/reset-usage`, { method: 'POST' }),
    },

    registration: {
      get: () => request<import('./types').RegistrationStatus>('/auth/registration'),
      toggle: (enabled: boolean) =>
        request<import('./types').RegistrationStatus>('/auth/registration', {
          method: 'POST',
          body: JSON.stringify({ enabled }),
        }),
    },

    ipLimit: {
      get: () => request<import('./types').IpLimitStatus>('/auth/ip-limit'),
      set: (limit: number) =>
        request<import('./types').IpLimitStatus>('/auth/ip-limit', {
          method: 'POST',
          body: JSON.stringify({ limit }),
        }),
    },

    css: {
      get: () => request<{ css: string }>('/auth/css'),
      save: (css: string) =>
        request<{ status: string }>('/auth/css', {
          method: 'PUT',
          body: JSON.stringify({ css }),
        }),
    },

    providers: {
      list: () => request<import('./types').ProviderConfig[]>('/auth/providers'),
      update: (key: string, data: { name?: string; base_url?: string }) =>
        request<{ status: string; provider: import('./types').ProviderConfig }>(`/auth/providers/${key}`, {
          method: 'PUT',
          body: JSON.stringify(data),
        }),
    },
  },

  models: {
    list: () => request<import('./types').ModelConfig[]>('/models'),
    get: (id: number) => request<import('./types').ModelConfig>(`/models/${id}`),
    create: (data: unknown) =>
      request<{ id: number; status: string }>('/models', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: unknown) =>
      request<{ status: string }>(`/models/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<{ status: string }>(`/models/${id}`, { method: 'DELETE' }),
    reorder: (modelIds: number[]) =>
      request<{ status: string }>('/models/reorder', {
        method: 'PUT',
        body: JSON.stringify({ model_ids: modelIds }),
      }),
    scan: () => request<import('./types').ScannedProvider[]>('/models/scan'),
  },

  conversations: {
    list: () => request<import('./types').Conversation[]>('/conversations'),
    create: (data?: unknown) =>
      request<import('./types').Conversation>('/conversations', {
        method: 'POST',
        body: JSON.stringify(data || {}),
      }),
    delete: (id: number) =>
      request<{ status: string }>(`/conversations/${id}`, { method: 'DELETE' }),
    messages: (id: number) =>
      request<import('./types').Message[]>(`/conversations/${id}/messages`),
    clearMessages: (id: number) =>
      request<{ status: string }>(`/conversations/${id}/messages`, {
        method: 'DELETE',
      }),
    branch: (id: number, messageIndex: number) =>
      request<import('./types').Conversation>(`/conversations/${id}/branch`, {
        method: 'POST',
        body: JSON.stringify({ message_index: messageIndex }),
      }),
  },

  config: {
    env: () => request<import('./types').EnvStatus>('/config/env'),
    updateEnv: (updates: Record<string, string>) =>
      request<{ status: string; updated_keys: string[] }>('/config/env', {
        method: 'POST',
        body: JSON.stringify({ updates }),
      }),
    status: () => request<import('./types').ConfigStatus>('/config/status'),
    uploads: {
      get: () => request<import('./types').FileUploadSettings>('/config/uploads'),
      update: (data: {
        file_upload_enabled?: boolean;
        ocr_enabled?: boolean;
        ocr_strategy?: string;
        whisper_model?: string;
        whisper_compute_type?: string;
        whisper_device?: string;
        whisper_language?: string | null;
        whisper_beam_size?: number;
      }) =>
        request<import('./types').FileUploadSettings>('/config/uploads', {
          method: 'PUT',
          body: JSON.stringify(data),
        }),
    },
  },

  subscriptions: {
    plans: {
      list: () => request<import('./types').SubscriptionPlan[]>('/subscriptions/plans'),
      public: () => request<import('./types').SubscriptionPlan[]>('/subscriptions/plans/public'),
      create: (data: { name: string; price_sats: number; duration_days: number; token_limit?: number | null; image_limit?: number | null; enabled?: boolean }) =>
        request<{ id: number; status: string }>('/subscriptions/plans', {
          method: 'POST',
          body: JSON.stringify(data),
        }),
      update: (id: number, data: { name?: string; price_sats?: number; duration_days?: number; token_limit?: number | null; image_limit?: number | null; enabled?: boolean }) =>
        request<{ status: string }>(`/subscriptions/plans/${id}`, {
          method: 'PUT',
          body: JSON.stringify(data),
        }),
      delete: (id: number) =>
        request<{ status: string }>(`/subscriptions/plans/${id}`, { method: 'DELETE' }),
      limits: (planId: number) => request<import('./types').PlanModelLimit[]>(`/subscriptions/plans/${planId}/limits`),
      setLimits: (planId: number, limits: { model_id: number; token_limit: number | null; image_limit?: number | null }[]) =>
        request<{ status: string }>(`/subscriptions/plans/${planId}/limits`, {
          method: 'PUT',
          body: JSON.stringify(limits),
        }),
    },
    my: () => request<import('./types').UserSubscription | null>('/subscriptions/my'),
    subscribe: (planId: number) =>
      request<import('./types').SubscribeResult>('/subscriptions/subscribe', {
        method: 'POST',
        body: JSON.stringify({ plan_id: planId }),
      }),
    checkPayment: (subscriptionId: number) =>
      request<{ status: string; plan_name?: string }>(`/subscriptions/check-payment/${subscriptionId}`, {
        method: 'POST',
      }),
    cancel: (subscriptionId: number) =>
      request<{ status: string }>(`/subscriptions/cancel/${subscriptionId}`, {
        method: 'POST',
      }),
    admin: {
      list: (userId?: number) => {
        const params = userId ? `?user_id=${userId}` : ''
        return request<import('./types').UserSubscription[]>(`/subscriptions/admin/all${params}`)
      },
      update: (id: number, status: string) =>
        request<{ status: string }>(`/subscriptions/admin/${id}?status=${status}`, {
          method: 'PUT',
        }),
      clearExpired: () =>
        request<{ deleted: number }>('/subscriptions/admin/expired', { method: 'DELETE' }),
    },
  },

  chat: {
    stream(convId: number, message: string, modelId?: number): EventSource {
      const params = new URLSearchParams({ conversation_id: String(convId), message });
      if (modelId) params.set('model_id', String(modelId));
      return new EventSource(`${BASE}/chat/stream?${params}`);
    },
    send: async function* (convId: number, message: string, modelId?: number, signal?: AbortSignal, attachments?: import('./types').AttachmentRecord[]) {
      const token = getToken();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const body: Record<string, unknown> = { conversation_id: convId, message, model_id: modelId };
      if (attachments && attachments.length > 0) {
        body.attachments = attachments;
      }

      const response = await fetch(`${BASE}/chat/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal,
      });
      if (!response.ok) {
        let detail = ''
        try { const err = await response.json(); detail = err.detail ? `: ${err.detail}` : '' } catch {}
        throw new Error(`Chat error ${response.status}${detail}`)
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') return;
            try {
              yield JSON.parse(data) as import('./types').StreamEvent;
            } catch { /* skip malformed */ }
          }
        }
      }
    },
    resume: async function* (convId: number, signal?: AbortSignal) {
      const token = getToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;

      const response = await fetch(`${BASE}/chat/resume/${convId}`, {
        method: 'POST',
        headers,
        signal,
      });
      if (!response.ok) {
        let detail = ''
        try { const err = await response.json(); detail = err.detail ? `: ${err.detail}` : '' } catch {}
        throw new Error(`Resume error ${response.status}${detail}`)
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') return;
            try {
              yield JSON.parse(data) as import('./types').StreamEvent;
            } catch { /* skip malformed */ }
          }
        }
      }
    },
    cancel: (convId: number) =>
      request<{ status: string }>(`/chat/cancel/${convId}`, { method: 'POST' }),
    generationStatus: (convId: number) =>
      request<import('./types').GenerateStatus>(`/conversations/${convId}/generation-status`),
    generateImage: async (convId: number, prompt: string, modelId: number, size: string = '1024x1024', n: number = 1) => {
      const token = getToken();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const response = await fetch(`${BASE}/chat/image`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ conversation_id: convId, prompt, model_id: modelId, size, n }),
      });
      if (!response.ok) {
        let detail = ''
        try { const err = await response.json(); detail = err.detail ? `: ${err.detail}` : '' } catch {}
        throw new Error(`Image generation error ${response.status}${detail}`)
      }
      return response.json() as Promise<{ images: string[]; revised_prompt?: string }>;
    },

    upload: async (file: File) => {
      const token = getToken();
      const headers: Record<string, string> = {};
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const formData = new FormData();
      formData.append('file', file);
      const response = await fetch(`${BASE}/chat/upload`, {
        method: 'POST',
        headers,
        body: formData,
      });
      if (!response.ok) {
        let detail = ''
        try { const err = await response.json(); detail = err.detail ? `: ${err.detail}` : '' } catch {}
        throw new Error(`Upload error ${response.status}${detail}`)
      }
      return response.json() as Promise<import('./types').UploadResponse>;
    },
  },
};
