import React, { useState, useEffect } from 'react'
import {
  Users, UserPlus, Trash2, Key, Wrench, Shield, Globe,
  Loader2, X, Check, Settings, RotateCcw, CreditCard, Plus, Edit3, Brain,
  ArrowUp, ArrowDown, Upload, File, Eye, Mic
} from 'lucide-react'
import { api } from '../api'
import type { User, ProviderConfig, ScannedProvider, ModelConfig, SubscriptionPlan, PlanModelLimit, UserSubscription as UserSub } from '../types'

interface Props {
  currentUser: User
  onClose: () => void
  onRefreshModels: () => void
}

type AdminTab = 'users' | 'providers' | 'models' | 'apikeys' | 'subscriptions' | 'uploads'

export default function AdminPanel({ currentUser, onClose, onRefreshModels }: Props) {
  const [tab, setTab] = useState<AdminTab>('users')

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-theme-bg-secondary rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col border border-theme-border-light" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-theme-border flex items-center justify-between shrink-0">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Shield className="w-5 h-5 text-theme-accent-text" /> Admin Panel
          </h2>
          <button onClick={onClose} className="px-3 py-1.5 hover:bg-theme-bg-hover rounded-lg text-sm">Close</button>
        </div>
        <div className="flex border-b border-theme-border shrink-0">
          {[
            { key: 'users' as AdminTab, icon: Users, label: 'Users' },
            { key: 'providers' as AdminTab, icon: Globe, label: 'Providers' },
            { key: 'models' as AdminTab, icon: Wrench, label: 'Models' },
            { key: 'apikeys' as AdminTab, icon: Key, label: 'API Keys' },
            { key: 'subscriptions' as AdminTab, icon: CreditCard, label: 'Subscriptions' },
            { key: 'uploads' as AdminTab, icon: Upload, label: 'Uploads' },
          ].map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 py-2.5 text-sm font-medium flex items-center justify-center gap-1.5 transition-colors ${
                tab === t.key ? 'text-theme-accent-text border-b-2 border-theme-accent-text bg-theme-bg-elevated/50' : 'text-theme-muted hover:text-theme-text'
              }`}
            >
              <t.icon className="w-4 h-4" /> {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {tab === 'users' && <UsersTab currentUser={currentUser} />}
          {tab === 'providers' && <ProvidersTab />}
          {tab === 'models' && <ModelsTab onRefresh={onRefreshModels} />}
          {tab === 'apikeys' && <ApiKeysTab />}
          {tab === 'subscriptions' && <SubscriptionsTab currentUser={currentUser} />}
          {tab === 'uploads' && <UploadsTab />}
        </div>
      </div>
    </div>
  )
}

function UsersTab({ currentUser }: { currentUser: User }) {
  const [users, setUsers] = useState<User[]>([])
  const [regEnabled, setRegEnabled] = useState(false)
  const [ipLimit, setIpLimit] = useState(3)
  const [ipLimitEdit, setIpLimitEdit] = useState(3)
  const [loading, setLoading] = useState(true)
  const [newUser, setNewUser] = useState('')
  const [newPass, setNewPass] = useState('')
  const [editingUser, setEditingUser] = useState<number | null>(null)
  const [editData, setEditData] = useState({ username: '', password: '', role: '', token_limit: '', image_limit: '' })
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    try {
      const [u, r, i] = await Promise.all([
        api.auth.users.list(),
        api.auth.registration.get(),
        api.auth.ipLimit.get(),
      ])
      setUsers(u)
      setRegEnabled(r.enabled)
      setIpLimit(i.limit)
      setIpLimitEdit(i.limit)
    } catch (e: any) { setError(e.message) }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!newUser.trim() || !newPass) { setError('Username and password required'); return }
    setError('')
    try {
      await api.auth.users.create(newUser.trim(), newPass)
      setNewUser('')
      setNewPass('')
      load()
    } catch (e: any) { setError(e.message) }
  }

  const handleUpdate = async (id: number) => {
    setError('')
    try {
      const updatePayload: any = {}
      if (editData.username) updatePayload.username = editData.username
      if (editData.password) updatePayload.password = editData.password
      if (editData.role) updatePayload.role = editData.role
      if (editData.token_limit !== '') {
        updatePayload.token_limit = editData.token_limit ? parseInt(editData.token_limit) : null
      }
      if (editData.image_limit !== '') {
        updatePayload.image_limit = editData.image_limit ? parseInt(editData.image_limit) : null
      }
      await api.auth.users.update(id, updatePayload)
      setEditingUser(null)
      load()
    } catch (e: any) { setError(e.message) }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this user?')) return
    try { await api.auth.users.delete(id); load() } catch (e: any) { setError(e.message) }
  }

  const handleRegistrationToggle = async () => {
    try {
      const r = await api.auth.registration.toggle(!regEnabled)
      setRegEnabled(r.enabled)
    } catch (e: any) { setError(e.message) }
  }

  const handleResetUsage = async (id: number) => {
    try {
      await api.auth.users.resetUsage(id)
      load()
    } catch (e: any) { setError(e.message) }
  }

  const handleIpLimitSave = async () => {
    try {
      const r = await api.auth.ipLimit.set(ipLimitEdit)
      setIpLimit(r.limit)
    } catch (e: any) { setError(e.message) }
  }

  return (
    <div className="space-y-4">
      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>}

      <div className="flex items-center justify-between">
        <div className="text-sm">
          <span className="text-theme-subtle">Registration: </span>
          <span className={`font-medium ${regEnabled ? 'text-theme-accent-text' : 'text-theme-muted'}`}>
            {regEnabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>
        <button
          onClick={handleRegistrationToggle}
          className={`w-9 h-5 rounded-full transition-colors relative ${regEnabled ? 'bg-theme-accent' : 'bg-theme-switch-off'}`}
        >
          <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${regEnabled ? 'left-4' : 'left-0.5'}`} />
        </button>
      </div>

      <div className="flex items-center gap-2">
        <div className="text-sm text-theme-subtle">IP account limit: </div>
        <input
          type="number"
          min={1}
          max={100}
          value={ipLimitEdit}
          onChange={e => setIpLimitEdit(parseInt(e.target.value) || 1)}
          className="bg-theme-bg-elevated rounded px-2 py-1 text-sm w-16 border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
        />
        <button
          onClick={handleIpLimitSave}
          disabled={ipLimitEdit === ipLimit}
          className="px-2 py-1 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted rounded text-xs"
        >
          Save
        </button>
      </div>

      <div className="flex gap-2">
        <input
          value={newUser}
          onChange={e => setNewUser(e.target.value)}
          placeholder="Username"
          className="flex-1 bg-theme-bg-elevated rounded-lg px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
        />
        <input
          value={newPass}
          onChange={e => setNewPass(e.target.value)}
          type="password"
          placeholder="Password"
          className="flex-1 bg-theme-bg-elevated rounded-lg px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
        />
        <button onClick={handleCreate} className="px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-sm">
          <UserPlus className="w-4 h-4" />
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>
      ) : (
        <div className="space-y-2">
          {users.map(u => (
            <div key={u.id} className="bg-theme-bg-elevated/40 rounded-lg px-3 py-2 flex items-center gap-3">
              {editingUser === u.id ? (
                <>
                  <input
                    value={editData.username}
                    onChange={e => setEditData(d => ({ ...d, username: e.target.value }))}
                    className="bg-theme-bg-elevated rounded px-2 py-1 text-sm w-32"
                  />
                  <input
                    value={editData.password}
                    onChange={e => setEditData(d => ({ ...d, password: e.target.value }))}
                    type="password"
                    placeholder="new pass"
                    className="bg-theme-bg-elevated rounded px-2 py-1 text-sm w-24"
                  />
                  {currentUser.role === 'owner' && (
                    <select
                      value={editData.role}
                      onChange={e => setEditData(d => ({ ...d, role: e.target.value }))}
                      className="bg-theme-bg-elevated rounded px-2 py-1 text-sm"
                    >
                      <option value="owner">Owner</option>
                      <option value="admin">Admin</option>
                      <option value="user">User</option>
                    </select>
                  )}
                  {currentUser.role === 'admin' && u.role !== 'owner' && (
                    <select
                      value={editData.role}
                      onChange={e => setEditData(d => ({ ...d, role: e.target.value }))}
                      className="bg-theme-bg-elevated rounded px-2 py-1 text-sm"
                    >
                      <option value="admin">Admin</option>
                      <option value="user">User</option>
                    </select>
                  )}
                  <input
                    value={editData.token_limit}
                    onChange={e => setEditData(d => ({ ...d, token_limit: e.target.value }))}
                    type="number"
                    placeholder="Token limit"
                    className="bg-theme-bg-elevated rounded px-2 py-1 text-sm w-28"
                  />
                  <input
                    value={editData.image_limit}
                    onChange={e => setEditData(d => ({ ...d, image_limit: e.target.value }))}
                    type="number"
                    placeholder="Image limit"
                    className="bg-theme-bg-elevated rounded px-2 py-1 text-sm w-28"
                  />
                  <button onClick={() => handleUpdate(u.id)} className="p-1 hover:bg-theme-accent rounded"><Check className="w-4 h-4 text-theme-accent-text" /></button>
                  <button onClick={() => setEditingUser(null)} className="p-1 hover:bg-theme-bg-active rounded"><X className="w-4 h-4 text-theme-subtle" /></button>
                </>
              ) : (
                <>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{u.username}</div>
                    <div className="text-xs flex items-center gap-2">
                      <span className={`${u.role === 'owner' ? 'text-theme-amber' : u.role === 'admin' ? 'text-theme-accent-text' : 'text-theme-muted'}`}>
                        {u.role}
                      </span>
                      {u.token_limit != null && (
                        <span className={u.token_usage >= u.token_limit ? 'text-theme-danger-text' : 'text-theme-muted'}>
                          Tokens: {u.token_usage.toLocaleString()} / {u.token_limit.toLocaleString()}
                        </span>
                      )}
                      {u.token_limit == null && (
                        <span className="text-theme-subtle">Tokens: {u.token_usage.toLocaleString()}</span>
                      )}
                    </div>
                  </div>
                  {(u.token_usage > 0) && (
                    <button
                      onClick={() => handleResetUsage(u.id)}
                      className="p-1.5 hover:bg-theme-accent/30 rounded text-theme-muted hover:text-theme-accent-text"
                      title="Reset token usage"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setEditingUser(u.id)
                      setEditData({
                        username: u.username,
                        password: '',
                        role: u.role,
                        token_limit: u.token_limit != null ? String(u.token_limit) : '',
                        image_limit: u.image_limit != null ? String(u.image_limit) : '',
                      })
                    }}
                    className="p-1.5 hover:bg-theme-bg-active rounded text-theme-muted hover:text-theme-text"
                  >
                    <Settings className="w-3.5 h-3.5" />
                  </button>
                  {currentUser.role === 'owner' ? (
                    u.id !== currentUser.id && (
                      <button onClick={() => handleDelete(u.id)} className="p-1.5 hover:bg-theme-danger/30 rounded text-theme-muted hover:text-theme-danger-text">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )
                  ) : (
                    u.role === 'user' && (
                      <button onClick={() => handleDelete(u.id)} className="p-1.5 hover:bg-theme-danger/30 rounded text-theme-muted hover:text-theme-danger-text">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ProvidersTab() {
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editUrl, setEditUrl] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    try { setProviders(await api.auth.providers.list()) } catch (e: any) { setError(e.message) }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const handleSave = async (key: string) => {
    setError('')
    try {
      await api.auth.providers.update(key, { name: editName, base_url: editUrl })
      setEditing(null)
      load()
    } catch (e: any) { setError(e.message) }
  }

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>

  return (
    <div className="space-y-3">
      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>}
      <p className="text-xs text-theme-muted">Customize how providers appear to users. Changes take effect immediately.</p>
      {providers.map(p => (
        <div key={p.key} className="bg-theme-bg-elevated/40 rounded-lg px-4 py-3">
          {editing === p.key ? (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-theme-muted">Display Name</label>
                <input
                  value={editName}
                  onChange={e => setEditName(e.target.value)}
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-theme-muted">Base URL</label>
                <input
                  value={editUrl}
                  onChange={e => setEditUrl(e.target.value)}
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
              <div className="flex gap-2">
                <button onClick={() => handleSave(p.key)} className="px-3 py-1 bg-theme-accent hover:bg-theme-accent-hover rounded text-sm">Save</button>
                <button onClick={() => setEditing(null)} className="px-3 py-1 hover:bg-theme-bg-active rounded text-sm">Cancel</button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-theme-muted font-mono">{p.base_url}</div>
              </div>
              <button
                onClick={() => { setEditing(p.key); setEditName(p.name); setEditUrl(p.base_url) }}
                className="p-1.5 hover:bg-theme-bg-active rounded text-theme-muted hover:text-theme-text"
              >
                <Settings className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

const PROVIDER_ICONS: Record<string, string> = {
  deepseek: 'DeepSeek', anthropic: 'Anthropic', minimax: 'MiniMax', openrouter: 'OpenRouter',
}

function ModelsTab({ onRefresh }: { onRefresh: () => void }) {
  const [scanned, setScanned] = useState<ScannedProvider[]>([])
  const [enabled, setEnabled] = useState<ModelConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [toggling, setToggling] = useState<Set<string>>(new Set())
  const [renaming, setRenaming] = useState<number | null>(null)
  const [renameVal, setRenameVal] = useState('')
  const [configuringId, setConfiguringId] = useState<number | null>(null)
  const [configForm, setConfigForm] = useState<{ temperature: number; max_tokens: number; thinking_enabled: boolean; thinking_budget_tokens: number; model_type: string; vision_enabled: boolean; tools_enabled: boolean }>({
    temperature: 0.7, max_tokens: 4096, thinking_enabled: false, thinking_budget_tokens: 4000, model_type: 'chat', vision_enabled: false, tools_enabled: true,
  })
  const [reordering, setReordering] = useState(false)

  const loadAll = async () => {
    setLoading(true)
    try {
      const [s, e] = await Promise.all([api.models.scan(), api.models.list()])
      setScanned(s)
      setEnabled(e)
    } catch (e) { console.error('loadAll failed:', e) }
    setLoading(false)
  }

  useEffect(() => { loadAll() }, [])

  const isEnabled = (provider: ScannedProvider, modelId: string) =>
    enabled.some(m => m.api_key_env === provider.env_var && m.model_name === modelId)

  const handleToggle = async (provider: ScannedProvider, modelId: string, turnOn: boolean) => {
    const key = `${provider.provider_key}:${modelId}`
    setToggling(prev => new Set(prev).add(key))
    try {
      if (turnOn) {
        const foundModel = provider.models.find(m => m.id === modelId)
        await api.models.create({
          name: `${provider.provider_name} — ${modelId}`,
          provider: provider.provider_type,
          model_name: modelId,
          model_type: foundModel?.suggested_type || 'chat',
          vision_enabled: foundModel?.supports_vision || false,
          base_url: provider.base_url,
          api_key_env: provider.env_var,
          temperature: 0.7,
          max_tokens: 4096,
          enabled: true,
        })
      } else {
        const ids = enabled
          .filter(m => m.api_key_env === provider.env_var && m.model_name === modelId)
          .map(m => m.id)
        await Promise.all(ids.map(id => api.models.delete(id).catch(() => {})))
      }
      await loadAll()
      onRefresh()
    } catch (e) { console.error('Toggle model failed:', e) }
    setToggling(prev => { const next = new Set(prev); next.delete(key); return next })
  }

  const handleRename = async (id: number) => {
    try {
      await api.models.update(id, { name: renameVal })
      setRenaming(null)
      loadAll()
      onRefresh()
    } catch (e) { console.error('Rename failed:', e) }
  }

  const startConfigure = (model: ModelConfig) => {
    setConfiguringId(model.id)
    setConfigForm({
      temperature: model.temperature,
      max_tokens: model.max_tokens,
      thinking_enabled: model.thinking_enabled,
      thinking_budget_tokens: model.thinking_budget_tokens || 4000,
      model_type: model.model_type || 'chat',
      vision_enabled: model.vision_enabled || false,
      tools_enabled: model.tools_enabled ?? true,
    })
  }

  const handleConfigure = async (id: number) => {
    try {
      await api.models.update(id, {
        temperature: configForm.temperature,
        max_tokens: configForm.max_tokens,
        thinking_enabled: configForm.thinking_enabled,
        thinking_budget_tokens: configForm.thinking_enabled ? configForm.thinking_budget_tokens : null,
        model_type: configForm.model_type,
        vision_enabled: configForm.vision_enabled,
        tools_enabled: configForm.tools_enabled,
      })
      setConfiguringId(null)
      loadAll()
      onRefresh()
    } catch (e) { console.error('Configure failed:', e) }
  }

  const enabledModels = enabled.filter(m => m.enabled).sort((a, b) => {
    if (a.sort_order == null && b.sort_order == null) return a.id - b.id
    if (a.sort_order == null) return 1
    if (b.sort_order == null) return -1
    return a.sort_order - b.sort_order
  })

  const handleMoveUp = async (idx: number) => {
    console.log('handleMoveUp called, idx:', idx, 'reordering:', reordering)
    if (idx <= 0 || reordering) return
    setReordering(true)
    try {
      const reordered = [...enabledModels]
      ;[reordered[idx - 1], reordered[idx]] = [reordered[idx], reordered[idx - 1]]
      const ids = reordered.map(m => m.id)
      console.log('Reordering model IDs:', ids)
      await api.models.reorder(ids)
      await loadAll()
      onRefresh()
    } catch (e) { console.error('Move up failed:', e) }
    finally { setReordering(false) }
  }

  const handleMoveDown = async (idx: number) => {
    console.log('handleMoveDown called, idx:', idx, 'reordering:', reordering)
    if (idx >= enabledModels.length - 1 || reordering) return
    setReordering(true)
    try {
      const reordered = [...enabledModels]
      ;[reordered[idx], reordered[idx + 1]] = [reordered[idx + 1], reordered[idx]]
      const ids = reordered.map(m => m.id)
      console.log('Reordering model IDs:', ids)
      await api.models.reorder(ids)
      await loadAll()
      onRefresh()
    } catch (e) { console.error('Move down failed:', e) }
    finally { setReordering(false) }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <button onClick={loadAll} disabled={loading} className="px-3 py-1.5 hover:bg-theme-bg-hover rounded-lg text-sm flex items-center gap-1">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
          Rescan
        </button>
      </div>

      {loading && scanned.length === 0 && (
        <div className="flex items-center justify-center py-12 text-theme-subtle">
          <Loader2 className="w-6 h-6 animate-spin mr-2" /> Scanning providers...
        </div>
      )}

      {!loading && scanned.length === 0 && (
        <div className="text-center text-theme-muted py-8">
          <Key className="w-12 h-12 mx-auto mb-3 text-theme-icon-muted" />
          <p>No API keys configured.</p>
          <p className="text-xs mt-1">Add API keys in Admin → API Keys, then rescan.</p>
        </div>
      )}

      {enabledModels.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="font-semibold text-sm text-theme-accent-text">Activated Models</h3>
            <span className="text-xs text-theme-muted">{enabledModels.length} active</span>
          </div>
          <div className="space-y-1">
            {enabledModels.map((model, idx) => {
              const providerInfo = scanned.find(p => p.env_var === model.api_key_env)
              const providerName = providerInfo?.provider_name || model.provider
              return (
                <div key={model.id}>
                  <div className="flex items-center gap-2 px-3 py-2 bg-theme-accent/20 border border-theme-accent/30 rounded-lg hover:bg-theme-accent/30 transition-colors">
                    <button
                      onClick={() => handleMoveUp(idx)}
                      disabled={idx === 0 || reordering}
                      className="p-0.5 hover:bg-theme-bg-active rounded text-theme-muted hover:text-theme-text disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                      title="Move up"
                    >
                      <ArrowUp className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => handleMoveDown(idx)}
                      disabled={idx === enabledModels.length - 1 || reordering}
                      className="p-0.5 hover:bg-theme-bg-active rounded text-theme-muted hover:text-theme-text disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                      title="Move down"
                    >
                      <ArrowDown className="w-3.5 h-3.5" />
                    </button>
                    <span className="text-xs text-theme-subtle w-5 text-center">{idx + 1}</span>
                    <div className="flex-1 min-w-0">
                      {renaming === model.id ? (
                        <div className="flex gap-1">
                          <input
                            value={renameVal}
                            onChange={e => setRenameVal(e.target.value)}
                            autoFocus
                            className="bg-theme-bg-elevated rounded px-2 py-0.5 text-sm w-full"
                            onKeyDown={e => { if (e.key === 'Enter') handleRename(model.id) }}
                          />
                          <button onClick={() => handleRename(model.id)} className="p-1 hover:bg-theme-accent rounded"><Check className="w-3.5 h-3.5 text-theme-accent-text" /></button>
                          <button onClick={() => setRenaming(null)} className="p-1 hover:bg-theme-bg-active rounded"><X className="w-3.5 h-3.5 text-theme-subtle" /></button>
                        </div>
                      ) : (
                        <div
                          className="text-sm font-medium truncate cursor-pointer hover:text-theme-accent-text"
                          onClick={() => { setRenaming(model.id); setRenameVal(model.name) }}
                          title="Click to rename"
                        >
                          {model.name}
                        </div>
                      )}
                      <div className="text-xs text-theme-muted truncate">{model.model_name} — {providerName}{model.model_type === 'image' ? ' — Image Gen' : ''}</div>
                    </div>
                    <button
                      onClick={() => configuringId === model.id ? setConfiguringId(null) : startConfigure(model)}
                      className="p-1.5 hover:bg-theme-bg-active rounded-lg transition-colors shrink-0"
                      title="Configure"
                    >
                      <Settings className="w-3.5 h-3.5 text-theme-subtle hover:text-theme-text" />
                    </button>
                    <button
                      onClick={async () => {
                        await api.models.delete(model.id).catch(() => {})
                        await loadAll()
                        onRefresh()
                      }}
                      className="p-1.5 hover:bg-theme-danger/30 rounded-lg transition-colors shrink-0"
                      title="Deactivate"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-theme-subtle hover:text-theme-danger-text" />
                    </button>
                  </div>
                  {configuringId === model.id && (
                    <div className="ml-0 mt-1 mb-2 p-3 bg-theme-bg-elevated/50 rounded-lg border border-theme-border-light/50 space-y-3">
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-theme-subtle w-24 shrink-0">Temperature</label>
                        <input
                          type="range" min="0" max="2" step="0.1"
                          value={configForm.temperature}
                          onChange={e => setConfigForm(f => ({ ...f, temperature: parseFloat(e.target.value) }))}
                          className="flex-1 h-1.5 rounded-full appearance-none bg-theme-switch-off accent-theme-focus-ring cursor-pointer"
                        />
                        <span className="text-xs text-theme-text-secondary w-8 text-right">{configForm.temperature.toFixed(1)}</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-theme-subtle w-24 shrink-0">Max Tokens</label>
                        <input
                          type="number" min="1" max="200000"
                          value={configForm.max_tokens}
                          onChange={e => setConfigForm(f => ({ ...f, max_tokens: parseInt(e.target.value) || 4096 }))}
                          className="flex-1 bg-theme-bg-hover rounded px-2 py-1 text-sm border border-theme-switch-off focus:outline-none focus:border-theme-focus-ring"
                        />
                      </div>
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-theme-subtle w-24 shrink-0 flex items-center gap-1">
                          <Brain className="w-3 h-3" /> Thinking
                        </label>
                        <button
                          onClick={() => setConfigForm(f => ({ ...f, thinking_enabled: !f.thinking_enabled }))}
                          className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${configForm.thinking_enabled ? 'bg-theme-purple' : 'bg-theme-switch-off'}`}
                        >
                          <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${configForm.thinking_enabled ? 'left-4' : 'left-0.5'}`} />
                        </button>
                        <span className="text-xs text-theme-muted">{configForm.thinking_enabled ? 'Enabled' : 'Disabled'}</span>
                      </div>
                      {configForm.thinking_enabled && (
                        <div className="flex items-center gap-4">
                          <label className="text-xs text-theme-subtle w-24 shrink-0">Budget Tokens</label>
                          <input
                            type="number" min="1024" max="100000"
                            value={configForm.thinking_budget_tokens}
                            onChange={e => setConfigForm(f => ({ ...f, thinking_budget_tokens: parseInt(e.target.value) || 4000 }))}
                            className="flex-1 bg-theme-bg-hover rounded px-2 py-1 text-sm border border-theme-switch-off focus:outline-none focus:border-theme-focus-ring"
                          />
                        </div>
                      )}
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-theme-subtle w-24 shrink-0">Model Type</label>
                        <select
                          value={configForm.model_type}
                          onChange={e => setConfigForm(f => ({ ...f, model_type: e.target.value }))}
                          className="flex-1 bg-theme-bg-hover rounded px-2 py-1 text-sm border border-theme-switch-off focus:outline-none focus:border-theme-focus-ring"
                        >
                          <option value="chat">Chat</option>
                          <option value="image">Image Generation</option>
                        </select>
                      </div>
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-theme-subtle w-24 shrink-0 flex items-center gap-1">
                          <Eye className="w-3 h-3" /> Vision
                        </label>
                        <button
                          onClick={() => setConfigForm(f => ({ ...f, vision_enabled: !f.vision_enabled }))}
                          className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${configForm.vision_enabled ? 'bg-theme-purple' : 'bg-theme-switch-off'}`}
                        >
                          <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${configForm.vision_enabled ? 'left-4' : 'left-0.5'}`} />
                        </button>
                        <span className="text-xs text-theme-muted">{configForm.vision_enabled ? 'Enabled' : 'Disabled'}</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-theme-subtle w-24 shrink-0 flex items-center gap-1">
                          <Wrench className="w-3 h-3" /> Tools
                        </label>
                        <button
                          onClick={() => setConfigForm(f => ({ ...f, tools_enabled: !f.tools_enabled }))}
                          className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${configForm.tools_enabled ? 'bg-theme-purple' : 'bg-theme-switch-off'}`}
                        >
                          <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${configForm.tools_enabled ? 'left-4' : 'left-0.5'}`} />
                        </button>
                        <span className="text-xs text-theme-muted">{configForm.tools_enabled ? 'Enabled' : 'Disabled'}</span>
                      </div>
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => handleConfigure(model.id)}
                          className="px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-xs font-medium"
                        >
                          Save Config
                        </button>
                        <button
                          onClick={() => setConfiguringId(null)}
                          className="px-3 py-1.5 hover:bg-theme-bg-active rounded-lg text-xs"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {scanned.map(provider => (
        <div key={provider.provider_key}>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="font-semibold text-sm">{provider.provider_name}</h3>
            {provider.error && <span className="text-xs text-theme-danger-text">Scan error: {provider.error}</span>}
            {!provider.error && <span className="text-xs text-theme-muted">{provider.models.length} models</span>}
          </div>
          {provider.error ? (
            <div className="text-xs text-theme-danger-text bg-theme-danger/20 rounded-lg px-3 py-2">Failed to fetch models: {provider.error}</div>
          ) : (
            <div className="space-y-1">
              {provider.models.map(model => {
                const on = isEnabled(provider, model.id)
                const busy = toggling.has(`${provider.provider_key}:${model.id}`)
                return (
                  <div key={model.id} className="flex items-center gap-3 px-3 py-2 bg-theme-bg-elevated/30 rounded-lg hover:bg-theme-bg-hover/50 transition-colors">
                    <button
                      onClick={() => handleToggle(provider, model.id, !on)}
                      disabled={busy}
                      className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${busy ? 'opacity-50' : ''} ${on ? 'bg-theme-accent' : 'bg-theme-switch-off'}`}
                    >
                      <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${on ? 'left-4' : 'left-0.5'}`} />
                    </button>
                    <div className="flex-1 min-w-0">
                      <div className={`text-sm font-medium truncate ${on ? 'text-theme-accent-text' : 'text-theme-muted'}`}>{model.name}</div>
                      <div className="text-xs text-theme-muted truncate">{model.id}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

function ApiKeysTab() {
  const [envStatus, setEnvStatus] = useState<{ configured: string[]; available: string[] } | null>(null)
  const [updates, setUpdates] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.config.env().then(setEnvStatus)
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.config.updateEnv(updates)
      setUpdates({})
      setEnvStatus(await api.config.env())
    } catch (e) {
      alert('Failed to update. Check server logs.')
    } finally {
      setSaving(false)
    }
  }

  const envLabels: Record<string, string> = {
    DEEPSEEK_API_KEY: 'DeepSeek API Key',
    ANTHROPIC_API_KEY: 'Anthropic Claude API Key',
    MINIMAX_API_KEY: 'MiniMax API Key',
    OPENROUTER_API_KEY: 'OpenRouter API Key',
    LNBITS_URL: 'LNBits Instance URL',
    LNBITS_INVOICE_KEY: 'LNBits Invoice Key',
  }

  const isPasswordField = (key: string) => !key.endsWith('_URL')

  return (
    <div className="space-y-4">
      <p className="text-xs text-theme-muted">Existing keys cannot be read — only overwritten. Leave blank to keep current value.</p>
      {(envStatus?.available || []).map(key => (
        <div key={key}>
          <label className="flex items-center gap-2 text-sm mb-1">
            <span>{envLabels[key] || key}</span>
            {envStatus?.configured.includes(key) && <span className="text-xs text-theme-accent-text">(configured)</span>}
          </label>
          <input
            type={isPasswordField(key) ? 'password' : 'text'}
            placeholder={envStatus?.configured.includes(key) ? '********' : isPasswordField(key) ? 'Enter...' : 'https://your-lnbits.com'}
            value={updates[key] || ''}
            onChange={e => setUpdates(u => ({ ...u, [key]: e.target.value }))}
            className="w-full bg-theme-bg-elevated rounded-lg px-3 py-2 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
          />
        </div>
      ))}
      <button
        onClick={handleSave}
        disabled={saving || Object.keys(updates).length === 0}
        className="w-full py-2 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted rounded-lg text-sm font-medium"
      >
        {saving ? 'Saving...' : 'Save Keys'}
      </button>
    </div>
  )
}

function SubscriptionsTab({ currentUser }: { currentUser: User }) {
  const [plans, setPlans] = useState<SubscriptionPlan[]>([])
  const [models, setModels] = useState<ModelConfig[]>([])
  const [subs, setSubs] = useState<UserSub[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [creating, setCreating] = useState(false)
  const [newPlan, setNewPlan] = useState({ name: '', price_sats: 0, duration_days: 30, token_limit: '', image_limit: '' })
  const [editingPlan, setEditingPlan] = useState<number | null>(null)
  const [editPlanData, setEditPlanData] = useState({ name: '', price_sats: 0, duration_days: 30, token_limit: '', image_limit: '' })
  const [limitsPlan, setLimitsPlan] = useState<number | null>(null)
  const [limits, setLimits] = useState<PlanModelLimit[]>([])
  const [limitValues, setLimitValues] = useState<Record<number, string>>({})
  const [limitImageValues, setLimitImageValues] = useState<Record<number, string>>({})
  const [savingLimits, setSavingLimits] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [p, m, s] = await Promise.all([
        api.subscriptions.plans.list(),
        api.models.list(),
        api.subscriptions.admin.list(),
      ])
      setPlans(p)
      setModels(m.filter(model => model.enabled))
      setSubs(s)
    } catch (e: any) { setError(e.message) }
    setLoading(false)
  }

  const loadLimits = async (planId: number) => {
    setLimitsPlan(planId)
    try {
      const data = await api.subscriptions.plans.limits(planId)
      setLimits(data)
      const vals: Record<number, string> = {}
      const imgVals: Record<number, string> = {}
      data.forEach(l => { vals[l.model_id] = l.token_limit?.toString() || ''; imgVals[l.model_id] = l.image_limit?.toString() || '' })
      setLimitValues(vals)
      setLimitImageValues(imgVals)
    } catch (e) { console.error('loadLimits failed:', e) }
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!newPlan.name) return
    setError('')
    try {
      await api.subscriptions.plans.create({
        name: newPlan.name,
        price_sats: newPlan.price_sats,
        duration_days: newPlan.duration_days,
        token_limit: newPlan.token_limit ? parseInt(newPlan.token_limit) : null,
        image_limit: newPlan.image_limit ? parseInt(newPlan.image_limit) : null,
      })
      setNewPlan({ name: '', price_sats: 0, duration_days: 30, token_limit: '', image_limit: '' })
      setCreating(false)
      load()
    } catch (e: any) { setError(e.message) }
  }

  const handleUpdate = async (id: number) => {
    setError('')
    try {
      await api.subscriptions.plans.update(id, {
        name: editPlanData.name,
        price_sats: editPlanData.price_sats,
        duration_days: editPlanData.duration_days,
        token_limit: editPlanData.token_limit ? parseInt(editPlanData.token_limit) : null,
        image_limit: editPlanData.image_limit ? parseInt(editPlanData.image_limit) : null,
      })
      setEditingPlan(null)
      load()
    } catch (e: any) { setError(e.message) }
  }

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete plan "${name}"?`)) return
    setError('')
    try {
      await api.subscriptions.plans.delete(id)
      load()
    } catch (e: any) { setError(e.message) }
  }

  const handleSetLimits = async () => {
    setSavingLimits(true)
    setError('')
    try {
      const entries = models.map(m => ({
        model_id: m.id,
        token_limit: limitValues[m.id] ? parseInt(limitValues[m.id]) : null,
        image_limit: limitImageValues[m.id] ? parseInt(limitImageValues[m.id]) : null,
      }))
      await api.subscriptions.plans.setLimits(limitsPlan!, entries)
      setLimitsPlan(null)
    } catch (e: any) { setError(e.message) }
    setSavingLimits(false)
  }

  const handleSubStatus = async (subId: number, status: string) => {
    try { await api.subscriptions.admin.update(subId, status); load() } catch (e: any) { setError(e.message) }
  }

  const handleClearExpired = async () => {
    if (!confirm('Delete all expired subscriptions? This cannot be undone.')) return
    setError('')
    try {
      const res = await api.subscriptions.admin.clearExpired()
      alert(`Deleted ${res.deleted} expired subscription(s)`)
      load()
    } catch (e: any) { setError(e.message) }
  }

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>

  return (
    <div className="space-y-6">
      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>}

      {/* Plans */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-theme-subtle">Plans</h3>
          <button
            onClick={() => setCreating(!creating)}
            className="px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-sm flex items-center gap-1"
          >
            <Plus className="w-3.5 h-3.5" /> New Plan
          </button>
        </div>

        {creating && (
          <div className="bg-theme-bg-elevated/40 rounded-lg p-4 mb-3 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-theme-muted">Name</label>
                <input
                  value={newPlan.name}
                  onChange={e => setNewPlan(p => ({ ...p, name: e.target.value }))}
                  placeholder="e.g. Pro"
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-theme-muted">Price (sats)</label>
                <input
                  type="number"
                  value={newPlan.price_sats}
                  onChange={e => setNewPlan(p => ({ ...p, price_sats: parseInt(e.target.value) || 0 }))}
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-theme-muted">Duration (days)</label>
                <input
                  type="number"
                  value={newPlan.duration_days}
                  onChange={e => setNewPlan(p => ({ ...p, duration_days: parseInt(e.target.value) || 0 }))}
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-theme-muted">Global token limit (optional)</label>
                <input
                  type="number"
                  value={newPlan.token_limit}
                  onChange={e => setNewPlan(p => ({ ...p, token_limit: e.target.value }))}
                  placeholder="Unlimited"
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-theme-muted">Global image limit (optional)</label>
                <input
                  type="number"
                  value={newPlan.image_limit}
                  onChange={e => setNewPlan(p => ({ ...p, image_limit: e.target.value }))}
                  placeholder="Unlimited"
                  className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={handleCreate} className="px-4 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded text-sm">Create</button>
              <button onClick={() => setCreating(false)} className="px-4 py-1.5 hover:bg-theme-bg-active rounded text-sm">Cancel</button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {plans.map(plan => (
            <div key={plan.id}>
              {editingPlan === plan.id ? (
                <div className="bg-theme-bg-elevated/40 rounded-lg p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-theme-muted">Name</label>
                      <input
                        value={editPlanData.name}
                        onChange={e => setEditPlanData(d => ({ ...d, name: e.target.value }))}
                        className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-theme-muted">Price (sats)</label>
                      <input
                        type="number"
                        value={editPlanData.price_sats}
                        onChange={e => setEditPlanData(d => ({ ...d, price_sats: parseInt(e.target.value) || 0 }))}
                        className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-theme-muted">Duration (days)</label>
                      <input
                        type="number"
                        value={editPlanData.duration_days}
                        onChange={e => setEditPlanData(d => ({ ...d, duration_days: parseInt(e.target.value) || 0 }))}
                        className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                      />
                    </div>
                  <div>
                    <label className="text-xs text-theme-muted">Global token limit (optional)</label>
                    <input
                      type="number"
                      value={editPlanData.token_limit}
                      onChange={e => setEditPlanData(d => ({ ...d, token_limit: e.target.value }))}
                      placeholder="Unlimited"
                      className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-theme-muted">Global image limit (optional)</label>
                    <input
                      type="number"
                      value={editPlanData.image_limit}
                      onChange={e => setEditPlanData(d => ({ ...d, image_limit: e.target.value }))}
                      placeholder="Unlimited"
                      className="w-full bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring mt-1"
                    />
                  </div>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => handleUpdate(plan.id)} className="px-4 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded text-sm">Save</button>
                    <button onClick={() => setEditingPlan(null)} className="px-4 py-1.5 hover:bg-theme-bg-active rounded text-sm">Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="bg-theme-bg-elevated/40 rounded-lg px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="font-medium">{plan.name}</span>
                      <span className="text-xs text-theme-accent-text">{plan.price_sats === 0 ? 'Free' : `${plan.price_sats.toLocaleString()} sats`}</span>
                      <span className="text-xs text-theme-muted">{plan.duration_days > 0 ? `${plan.duration_days}d` : 'Unlimited'}</span>
                      {plan.token_limit && <span className="text-xs text-theme-muted">{plan.token_limit.toLocaleString()} tokens</span>}
                      {plan.image_limit && <span className="text-xs text-theme-muted">{plan.image_limit.toLocaleString()} images</span>}
                      {!plan.enabled && <span className="text-xs px-1.5 py-0.5 bg-theme-danger/30 text-theme-danger-text rounded">Disabled</span>}
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => limitsPlan === plan.id ? setLimitsPlan(null) : loadLimits(plan.id)}
                        className={`p-1.5 rounded text-xs ${limitsPlan === plan.id ? 'bg-theme-accent/30 text-theme-accent-text' : 'hover:bg-theme-bg-active text-theme-muted hover:text-theme-text'}`}
                        title="Per-model limits"
                      >
                        <Wrench className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => {
                          setEditingPlan(plan.id)
                          setEditPlanData({
                            name: plan.name,
                            price_sats: plan.price_sats,
                            duration_days: plan.duration_days,
                            token_limit: plan.token_limit?.toString() || '',
                            image_limit: plan.image_limit?.toString() || '',
                          })
                        }}
                        className="p-1.5 hover:bg-theme-bg-active rounded text-theme-muted hover:text-theme-text"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      {plan.name !== 'Free' && (
                        <button
                          onClick={() => handleDelete(plan.id, plan.name)}
                          className="p-1.5 hover:bg-theme-danger/30 rounded text-theme-muted hover:text-theme-danger-text"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>

                  {limitsPlan === plan.id && (
                    <div className="mt-3 pt-3 border-t border-theme-border-light space-y-2">
                      <p className="text-xs text-theme-muted">Per-model limits for <span className="text-theme-accent-text">{plan.name}</span>. Empty = no limit.</p>
                      {models.length === 0 ? (
                        <p className="text-xs text-theme-muted">No enabled models</p>
                      ) : (
                        <>
                          {models.map(m => (
                            <div key={m.id} className="flex items-center gap-2">
                              <span className="text-xs text-theme-subtle w-32 truncate">{m.name}</span>
                              <input
                                type="number"
                                value={limitValues[m.id] || ''}
                                onChange={e => setLimitValues(v => ({ ...v, [m.id]: e.target.value }))}
                                placeholder="Token lim"
                                className="w-20 bg-theme-bg-elevated rounded px-2 py-1 text-xs border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
                              />
                              <input
                                type="number"
                                value={limitImageValues[m.id] || ''}
                                onChange={e => setLimitImageValues(v => ({ ...v, [m.id]: e.target.value }))}
                                placeholder="Image lim"
                                className="w-20 bg-theme-bg-elevated rounded px-2 py-1 text-xs border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
                              />
                            </div>
                          ))}
                          <button onClick={handleSetLimits} disabled={savingLimits} className="px-4 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded text-sm disabled:opacity-50">
                            {savingLimits ? 'Saving...' : 'Save Limits'}
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Active Subscriptions */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-theme-subtle">Active Subscriptions</h3>
          <button
            onClick={handleClearExpired}
            className="px-3 py-1.5 bg-theme-danger/20 hover:bg-theme-danger/40 rounded-lg text-xs text-theme-danger-text flex items-center gap-1"
          >
            <Trash2 className="w-3.5 h-3.5" /> Clear Expired
          </button>
        </div>
        {subs.length === 0 ? (
          <p className="text-xs text-theme-muted">No subscriptions yet.</p>
        ) : (
          <div className="space-y-1">
            {subs.map(sub => {
              const daysLeft = sub.expires_at
                ? Math.max(0, Math.ceil((new Date(sub.expires_at).getTime() - Date.now()) / (1000 * 60 * 60 * 24)))
                : 0
              return (
                <div key={sub.id} className="flex items-center justify-between bg-theme-bg-elevated/30 rounded-lg px-3 py-2">
                  <div>
                    <span className="text-sm">{sub.plan_name || `Plan #${sub.plan_id}`}</span>
                    <span className={`ml-2 text-xs px-1.5 py-0.5 rounded ${
                      sub.status === 'active' ? 'bg-theme-accent/30 text-theme-accent-text' :
                      sub.status === 'pending' ? 'bg-theme-amber/30 text-theme-amber' :
                      'bg-theme-danger/30 text-theme-danger-text'
                    }`}>{sub.status}</span>
                    <span className="text-xs text-theme-muted ml-2">User #{sub.user_id}</span>
                    {sub.expires_at && sub.status === 'active' && (
                      <span className="text-xs text-theme-muted ml-2">{daysLeft}d left</span>
                    )}
                  </div>
                  <div className="flex gap-1">
                    <button
                      onClick={() => handleSubStatus(sub.id, 'active')}
                      className="px-2 py-1 text-xs hover:bg-theme-accent/30 rounded text-theme-muted hover:text-theme-accent-text"
                    >Activate</button>
                    <button
                      onClick={() => handleSubStatus(sub.id, 'expired')}
                      className="px-2 py-1 text-xs hover:bg-theme-danger/30 rounded text-theme-muted hover:text-theme-danger-text"
                    >Expire</button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function UploadsTab() {
  const [settings, setSettings] = useState<import('../types').FileUploadSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    try { setSettings(await api.config.uploads.get()) } catch (e: any) { setError(e.message) }
  }

  useEffect(() => { load() }, [])

  const handleSave = async (key: string, value: boolean | string | number | null) => {
    setSaving(true)
    setError('')
    try {
      const update: Record<string, boolean | string | number | null> = {}
      update[key] = value
      const updated = await api.config.uploads.update(update)
      setSettings(updated)
    } catch (e: any) { setError(e.message) }
    setSaving(false)
  }

  if (!settings) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>

  return (
    <div className="space-y-6">
      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>}

      <div>
        <h3 className="text-sm font-semibold text-theme-subtle mb-3">File Upload Configuration</h3>
        <p className="text-xs text-theme-muted mb-4">Control how file uploads are handled. Changes apply immediately to all users.</p>

        <div className="bg-theme-bg-elevated/40 rounded-lg p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium flex items-center gap-2">
                <Upload className="w-4 h-4 text-theme-accent-text" />
                Enable File Uploads
              </div>
              <p className="text-xs text-theme-muted mt-1">Allow users to upload images, documents, and code files in chat</p>
            </div>
            <button
              onClick={() => handleSave('file_upload_enabled', !settings.file_upload_enabled)}
              disabled={saving}
              className={`w-9 h-5 rounded-full transition-colors relative ${saving ? 'opacity-50' : ''} ${settings.file_upload_enabled ? 'bg-theme-accent' : 'bg-theme-switch-off'}`}
            >
              <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${settings.file_upload_enabled ? 'left-4' : 'left-0.5'}`} />
            </button>
          </div>
        </div>

        <div className="mt-4 bg-theme-bg-elevated/40 rounded-lg p-4 space-y-4">
          <div>
            <div className="text-sm font-medium flex items-center gap-2">
              <Mic className="w-4 h-4 text-theme-blue-text" />
              Speech-to-Text (faster-whisper, multilingual)
            </div>
            <p className="text-xs text-theme-muted mt-1">Picks the Whisper model used for voice transcription. All sizes are multilingual. Larger models are more accurate but slower and use more RAM.</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs text-theme-muted">Provider</span>
              <select
                value={settings.whisper_provider || 'local'}
                disabled={saving}
                onChange={e => handleSave('whisper_provider', e.target.value)}
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              >
                <option value="local">local — faster-whisper (on-device, free)</option>
                <option value="openrouter">openrouter — Whisper via OpenRouter API</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-theme-muted">OpenRouter model (used when provider = openrouter)</span>
              <input
                value={settings.whisper_openrouter_model || ''}
                disabled={saving}
                onChange={e => handleSave('whisper_openrouter_model', e.target.value || 'openai/whisper-1')}
                placeholder="openai/whisper-1"
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              />
            </label>
            <p className="text-xs text-theme-muted sm:col-span-2">
              The OpenRouter provider requires <code>OPENROUTER_API_KEY</code> in <code>data/.env</code>. Local whisper settings below are still saved and used when the provider is set to <code>local</code>.
            </p>

            <label className="block">
              <span className="text-xs text-theme-muted">Model</span>
              <select
                value={settings.whisper_model}
                disabled={saving}
                onChange={e => handleSave('whisper_model', e.target.value)}
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              >
                <option value="tiny">tiny — fastest, ~75MB (real-time on CPU)</option>
                <option value="base">base — fast, ~142MB</option>
                <option value="small">small — accurate, ~466MB</option>
                <option value="medium">medium — more accurate, ~1.5GB</option>
                <option value="large-v3">large-v3 — most accurate, ~3.1GB</option>
                <option value="distil-large-v3">distil-large-v3 — fast large, ~1.5GB</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-theme-muted">Beam size (1 = greedy, fastest)</span>
              <input
                type="number"
                min={1}
                max={10}
                value={settings.whisper_beam_size}
                disabled={saving}
                onChange={e => handleSave('whisper_beam_size', Math.max(1, Math.min(10, parseInt(e.target.value || '1', 10))))}
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              />
            </label>
            <label className="block">
              <span className="text-xs text-theme-muted">Compute type</span>
              <select
                value={settings.whisper_compute_type}
                disabled={saving}
                onChange={e => handleSave('whisper_compute_type', e.target.value)}
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              >
                <option value="int8">int8 — best on CPU (default)</option>
                <option value="int8_float16">int8_float16 — mixed (CUDA)</option>
                <option value="float16">float16 — GPU</option>
                <option value="float32">float32 — max precision</option>
                <option value="bfloat16">bfloat16 — newer GPU</option>
                <option value="int16">int16 — CPU alt</option>
              </select>
            </label>
            <label className="block">
              <span className="text-xs text-theme-muted">Device</span>
              <select
                value={settings.whisper_device || 'auto'}
                disabled={saving}
                onChange={e => handleSave('whisper_device', e.target.value)}
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              >
                <option value="auto">auto (cuda if available, else cpu)</option>
                <option value="cpu">cpu</option>
                <option value="cuda">cuda</option>
              </select>
            </label>
            <label className="block sm:col-span-2">
              <span className="text-xs text-theme-muted">Language (optional)</span>
              <input
                value={settings.whisper_language || ''}
                disabled={saving}
                onChange={e => handleSave('whisper_language', e.target.value || null)}
                placeholder="auto-detect (en, de, fr, ...)"
                className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
              />
            </label>
          </div>
        </div>

        <div className="mt-4 bg-theme-bg-elevated/40 rounded-lg p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium flex items-center gap-2">
                <File className="w-4 h-4 text-theme-amber" />
                Enable OCR (Tesseract)
              </div>
              <p className="text-xs text-theme-muted mt-1">Auto-extract text from images using Tesseract OCR when the AI model doesn't support vision</p>
            </div>
            <button
              onClick={() => handleSave('ocr_enabled', !settings.ocr_enabled)}
              disabled={saving}
              className={`w-9 h-5 rounded-full transition-colors relative ${saving ? 'opacity-50' : ''} ${settings.ocr_enabled ? 'bg-theme-accent' : 'bg-theme-switch-off'}`}
            >
              <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${settings.ocr_enabled ? 'left-4' : 'left-0.5'}`} />
            </button>
          </div>
        </div>

        <div className="mt-4 bg-theme-bg-elevated/40 rounded-lg p-4 space-y-4">
          <div>
            <div className="text-sm font-medium flex items-center gap-2 mb-2">
              <Eye className="w-4 h-4 text-theme-purple" />
              Image Handling for Non-Vision Models
            </div>
            <p className="text-xs text-theme-muted mb-3">What happens when a user uploads an image to a model that doesn't support vision:</p>
            <div className="space-y-2">
              <label className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition-colors ${settings.ocr_strategy === 'ocr' ? 'bg-theme-accent/30 border border-theme-accent/40' : 'bg-theme-bg-elevated/50 border border-theme-border-light/30'}`}>
                <input
                  type="radio"
                  name="ocr_strategy"
                  value="ocr"
                  checked={settings.ocr_strategy === 'ocr'}
                  onChange={() => handleSave('ocr_strategy', 'ocr')}
                  className="accent-theme-focus-ring"
                />
                <div>
                  <div className="text-sm">OCR Fallback</div>
                  <div className="text-xs text-theme-muted">Extract text from images using Tesseract OCR and pass it to the AI (recommended)</div>
                </div>
              </label>
              <label className={`flex items-center gap-3 px-3 py-2 rounded-lg cursor-pointer transition-colors ${settings.ocr_strategy === 'deny' ? 'bg-theme-danger/30 border border-theme-danger/40' : 'bg-theme-bg-elevated/50 border border-theme-border-light/30'}`}>
                <input
                  type="radio"
                  name="ocr_strategy"
                  value="deny"
                  checked={settings.ocr_strategy === 'deny'}
                  onChange={() => handleSave('ocr_strategy', 'deny')}
                  className="accent-theme-danger"
                />
                <div>
                  <div className="text-sm">Deny Image Uploads</div>
                  <div className="text-xs text-theme-muted">Block image uploads for non-vision models. Users must use a vision-capable model</div>
                </div>
              </label>
            </div>
          </div>
        </div>

        <div className="mt-4 p-4 bg-theme-accent/10 border border-theme-accent/20 rounded-lg">
          <div className="text-xs text-theme-accent-text font-medium mb-1">What's a Vision Model?</div>
          <p className="text-xs text-theme-accent-text/70">Vision-capable models can "see" and understand images directly. Examples: GPT-4o, Claude 3.5 Sonnet, Gemini Pro Vision, Qwen-VL. These models bypass OCR entirely for more accurate image understanding.</p>
          <p className="text-xs text-theme-accent-text/70 mt-1">Set each model's Vision capability in the <span className="text-theme-accent-text">Models</span> tab.</p>
        </div>
      </div>
    </div>
  )
}
