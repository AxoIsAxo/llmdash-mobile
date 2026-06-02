import React, { useState, useEffect } from 'react'
import {
  Users, UserPlus, Trash2, Key, Wrench, Shield, Globe,
  Loader2, X, Check, Settings, RotateCcw, CreditCard, Plus, Edit3, Brain,
  ArrowUp, ArrowDown
} from 'lucide-react'
import { api } from '../api'
import type { User, ProviderConfig, ScannedProvider, ModelConfig, SubscriptionPlan, PlanModelLimit, UserSubscription as UserSub } from '../types'

interface Props {
  currentUser: User
  onClose: () => void
  onRefreshModels: () => void
}

type AdminTab = 'users' | 'providers' | 'models' | 'apikeys' | 'subscriptions'

export default function AdminPanel({ currentUser, onClose, onRefreshModels }: Props) {
  const [tab, setTab] = useState<AdminTab>('users')

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-gray-900 rounded-2xl w-full max-w-3xl max-h-[85vh] flex flex-col border border-gray-700" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-gray-800 flex items-center justify-between shrink-0">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Shield className="w-5 h-5 text-emerald-400" /> Admin Panel
          </h2>
          <button onClick={onClose} className="px-3 py-1.5 hover:bg-gray-800 rounded-lg text-sm">Close</button>
        </div>
        <div className="flex border-b border-gray-800 shrink-0">
          {[
            { key: 'users' as AdminTab, icon: Users, label: 'Users' },
            { key: 'providers' as AdminTab, icon: Globe, label: 'Providers' },
            { key: 'models' as AdminTab, icon: Wrench, label: 'Models' },
            { key: 'apikeys' as AdminTab, icon: Key, label: 'API Keys' },
            { key: 'subscriptions' as AdminTab, icon: CreditCard, label: 'Subscriptions' },
          ].map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 py-2.5 text-sm font-medium flex items-center justify-center gap-1.5 transition-colors ${
                tab === t.key ? 'text-emerald-400 border-b-2 border-emerald-400 bg-gray-800/50' : 'text-gray-500 hover:text-gray-300'
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
  const [editData, setEditData] = useState({ username: '', password: '', role: '', token_limit: '' })
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
      {error && <div className="bg-red-900/30 border border-red-800 rounded-lg px-3 py-2 text-sm text-red-300">{error}</div>}

      <div className="flex items-center justify-between">
        <div className="text-sm">
          <span className="text-gray-400">Registration: </span>
          <span className={`font-medium ${regEnabled ? 'text-emerald-400' : 'text-gray-500'}`}>
            {regEnabled ? 'Enabled' : 'Disabled'}
          </span>
        </div>
        <button
          onClick={handleRegistrationToggle}
          className={`w-9 h-5 rounded-full transition-colors relative ${regEnabled ? 'bg-emerald-600' : 'bg-gray-600'}`}
        >
          <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${regEnabled ? 'left-4' : 'left-0.5'}`} />
        </button>
      </div>

      <div className="flex items-center gap-2">
        <div className="text-sm text-gray-400">IP account limit: </div>
        <input
          type="number"
          min={1}
          max={100}
          value={ipLimitEdit}
          onChange={e => setIpLimitEdit(parseInt(e.target.value) || 1)}
          className="bg-gray-800 rounded px-2 py-1 text-sm w-16 border border-gray-700 focus:outline-none focus:border-emerald-500"
        />
        <button
          onClick={handleIpLimitSave}
          disabled={ipLimitEdit === ipLimit}
          className="px-2 py-1 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs"
        >
          Save
        </button>
      </div>

      <div className="flex gap-2">
        <input
          value={newUser}
          onChange={e => setNewUser(e.target.value)}
          placeholder="Username"
          className="flex-1 bg-gray-800 rounded-lg px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500"
        />
        <input
          value={newPass}
          onChange={e => setNewPass(e.target.value)}
          type="password"
          placeholder="Password"
          className="flex-1 bg-gray-800 rounded-lg px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500"
        />
        <button onClick={handleCreate} className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm">
          <UserPlus className="w-4 h-4" />
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-gray-400" /></div>
      ) : (
        <div className="space-y-2">
          {users.map(u => (
            <div key={u.id} className="bg-gray-800/40 rounded-lg px-3 py-2 flex items-center gap-3">
              {editingUser === u.id ? (
                <>
                  <input
                    value={editData.username}
                    onChange={e => setEditData(d => ({ ...d, username: e.target.value }))}
                    className="bg-gray-800 rounded px-2 py-1 text-sm w-32"
                  />
                  <input
                    value={editData.password}
                    onChange={e => setEditData(d => ({ ...d, password: e.target.value }))}
                    type="password"
                    placeholder="new pass"
                    className="bg-gray-800 rounded px-2 py-1 text-sm w-24"
                  />
                  {currentUser.role === 'owner' && (
                    <select
                      value={editData.role}
                      onChange={e => setEditData(d => ({ ...d, role: e.target.value }))}
                      className="bg-gray-800 rounded px-2 py-1 text-sm"
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
                      className="bg-gray-800 rounded px-2 py-1 text-sm"
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
                    className="bg-gray-800 rounded px-2 py-1 text-sm w-28"
                  />
                  <button onClick={() => handleUpdate(u.id)} className="p-1 hover:bg-emerald-800 rounded"><Check className="w-4 h-4 text-emerald-400" /></button>
                  <button onClick={() => setEditingUser(null)} className="p-1 hover:bg-gray-700 rounded"><X className="w-4 h-4 text-gray-400" /></button>
                </>
              ) : (
                <>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{u.username}</div>
                    <div className="text-xs flex items-center gap-2">
                      <span className={`${u.role === 'owner' ? 'text-amber-400' : u.role === 'admin' ? 'text-emerald-400' : 'text-gray-500'}`}>
                        {u.role}
                      </span>
                      {u.token_limit != null && (
                        <span className={u.token_usage >= u.token_limit ? 'text-red-400' : 'text-gray-500'}>
                          Tokens: {u.token_usage.toLocaleString()} / {u.token_limit.toLocaleString()}
                        </span>
                      )}
                      {u.token_limit == null && (
                        <span className="text-gray-600">Tokens: {u.token_usage.toLocaleString()}</span>
                      )}
                    </div>
                  </div>
                  {(u.token_usage > 0) && (
                    <button
                      onClick={() => handleResetUsage(u.id)}
                      className="p-1.5 hover:bg-emerald-900/30 rounded text-gray-500 hover:text-emerald-400"
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
                      })
                    }}
                    className="p-1.5 hover:bg-gray-700 rounded text-gray-500 hover:text-gray-300"
                  >
                    <Settings className="w-3.5 h-3.5" />
                  </button>
                  {currentUser.role === 'owner' ? (
                    u.id !== currentUser.id && (
                      <button onClick={() => handleDelete(u.id)} className="p-1.5 hover:bg-red-900/30 rounded text-gray-500 hover:text-red-400">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )
                  ) : (
                    u.role === 'user' && (
                      <button onClick={() => handleDelete(u.id)} className="p-1.5 hover:bg-red-900/30 rounded text-gray-500 hover:text-red-400">
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

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-gray-400" /></div>

  return (
    <div className="space-y-3">
      {error && <div className="bg-red-900/30 border border-red-800 rounded-lg px-3 py-2 text-sm text-red-300">{error}</div>}
      <p className="text-xs text-gray-500">Customize how providers appear to users. Changes take effect immediately.</p>
      {providers.map(p => (
        <div key={p.key} className="bg-gray-800/40 rounded-lg px-4 py-3">
          {editing === p.key ? (
            <div className="space-y-2">
              <div>
                <label className="text-xs text-gray-500">Display Name</label>
                <input
                  value={editName}
                  onChange={e => setEditName(e.target.value)}
                  className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">Base URL</label>
                <input
                  value={editUrl}
                  onChange={e => setEditUrl(e.target.value)}
                  className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                />
              </div>
              <div className="flex gap-2">
                <button onClick={() => handleSave(p.key)} className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 rounded text-sm">Save</button>
                <button onClick={() => setEditing(null)} className="px-3 py-1 hover:bg-gray-700 rounded text-sm">Cancel</button>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-gray-500 font-mono">{p.base_url}</div>
              </div>
              <button
                onClick={() => { setEditing(p.key); setEditName(p.name); setEditUrl(p.base_url) }}
                className="p-1.5 hover:bg-gray-700 rounded text-gray-500 hover:text-gray-300"
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
  const [configForm, setConfigForm] = useState<{ temperature: number; max_tokens: number; thinking_enabled: boolean; thinking_budget_tokens: number }>({
    temperature: 0.7, max_tokens: 4096, thinking_enabled: false, thinking_budget_tokens: 4000,
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
        await api.models.create({
          name: `${provider.provider_name} — ${modelId}`,
          provider: provider.provider_type,
          model_name: modelId,
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
    })
  }

  const handleConfigure = async (id: number) => {
    try {
      await api.models.update(id, {
        temperature: configForm.temperature,
        max_tokens: configForm.max_tokens,
        thinking_enabled: configForm.thinking_enabled,
        thinking_budget_tokens: configForm.thinking_enabled ? configForm.thinking_budget_tokens : null,
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
        <button onClick={loadAll} disabled={loading} className="px-3 py-1.5 hover:bg-gray-800 rounded-lg text-sm flex items-center gap-1">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
          Rescan
        </button>
      </div>

      {loading && scanned.length === 0 && (
        <div className="flex items-center justify-center py-12 text-gray-400">
          <Loader2 className="w-6 h-6 animate-spin mr-2" /> Scanning providers...
        </div>
      )}

      {!loading && scanned.length === 0 && (
        <div className="text-center text-gray-500 py-8">
          <Key className="w-12 h-12 mx-auto mb-3 text-gray-700" />
          <p>No API keys configured.</p>
          <p className="text-xs mt-1">Add API keys in Admin → API Keys, then rescan.</p>
        </div>
      )}

      {enabledModels.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <h3 className="font-semibold text-sm text-emerald-400">Activated Models</h3>
            <span className="text-xs text-gray-500">{enabledModels.length} active</span>
          </div>
          <div className="space-y-1">
            {enabledModels.map((model, idx) => {
              const providerInfo = scanned.find(p => p.env_var === model.api_key_env)
              const providerName = providerInfo?.provider_name || model.provider
              return (
                <div key={model.id}>
                  <div className="flex items-center gap-2 px-3 py-2 bg-emerald-900/20 border border-emerald-800/30 rounded-lg hover:bg-emerald-900/30 transition-colors">
                    <button
                      onClick={() => handleMoveUp(idx)}
                      disabled={idx === 0 || reordering}
                      className="p-0.5 hover:bg-gray-700 rounded text-gray-500 hover:text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                      title="Move up"
                    >
                      <ArrowUp className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => handleMoveDown(idx)}
                      disabled={idx === enabledModels.length - 1 || reordering}
                      className="p-0.5 hover:bg-gray-700 rounded text-gray-500 hover:text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                      title="Move down"
                    >
                      <ArrowDown className="w-3.5 h-3.5" />
                    </button>
                    <span className="text-xs text-gray-600 w-5 text-center">{idx + 1}</span>
                    <div className="flex-1 min-w-0">
                      {renaming === model.id ? (
                        <div className="flex gap-1">
                          <input
                            value={renameVal}
                            onChange={e => setRenameVal(e.target.value)}
                            autoFocus
                            className="bg-gray-800 rounded px-2 py-0.5 text-sm w-full"
                            onKeyDown={e => { if (e.key === 'Enter') handleRename(model.id) }}
                          />
                          <button onClick={() => handleRename(model.id)} className="p-1 hover:bg-emerald-800 rounded"><Check className="w-3.5 h-3.5 text-emerald-400" /></button>
                          <button onClick={() => setRenaming(null)} className="p-1 hover:bg-gray-700 rounded"><X className="w-3.5 h-3.5 text-gray-400" /></button>
                        </div>
                      ) : (
                        <div
                          className="text-sm font-medium truncate cursor-pointer hover:text-emerald-400"
                          onClick={() => { setRenaming(model.id); setRenameVal(model.name) }}
                          title="Click to rename"
                        >
                          {model.name}
                        </div>
                      )}
                      <div className="text-xs text-gray-500 truncate">{model.model_name} — {providerName}</div>
                    </div>
                    <button
                      onClick={() => configuringId === model.id ? setConfiguringId(null) : startConfigure(model)}
                      className="p-1.5 hover:bg-gray-700 rounded-lg transition-colors shrink-0"
                      title="Configure"
                    >
                      <Settings className="w-3.5 h-3.5 text-gray-400 hover:text-gray-200" />
                    </button>
                    <button
                      onClick={async () => {
                        await api.models.delete(model.id).catch(() => {})
                        await loadAll()
                        onRefresh()
                      }}
                      className="p-1.5 hover:bg-red-800/30 rounded-lg transition-colors shrink-0"
                      title="Deactivate"
                    >
                      <Trash2 className="w-3.5 h-3.5 text-gray-400 hover:text-red-400" />
                    </button>
                  </div>
                  {configuringId === model.id && (
                    <div className="ml-0 mt-1 mb-2 p-3 bg-gray-800/50 rounded-lg border border-gray-700/50 space-y-3">
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-gray-400 w-24 shrink-0">Temperature</label>
                        <input
                          type="range" min="0" max="2" step="0.1"
                          value={configForm.temperature}
                          onChange={e => setConfigForm(f => ({ ...f, temperature: parseFloat(e.target.value) }))}
                          className="flex-1 h-1.5 rounded-full appearance-none bg-gray-600 accent-emerald-500 cursor-pointer"
                        />
                        <span className="text-xs text-gray-300 w-8 text-right">{configForm.temperature.toFixed(1)}</span>
                      </div>
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-gray-400 w-24 shrink-0">Max Tokens</label>
                        <input
                          type="number" min="1" max="200000"
                          value={configForm.max_tokens}
                          onChange={e => setConfigForm(f => ({ ...f, max_tokens: parseInt(e.target.value) || 4096 }))}
                          className="flex-1 bg-gray-700 rounded px-2 py-1 text-sm border border-gray-600 focus:outline-none focus:border-emerald-500"
                        />
                      </div>
                      <div className="flex items-center gap-4">
                        <label className="text-xs text-gray-400 w-24 shrink-0 flex items-center gap-1">
                          <Brain className="w-3 h-3" /> Thinking
                        </label>
                        <button
                          onClick={() => setConfigForm(f => ({ ...f, thinking_enabled: !f.thinking_enabled }))}
                          className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${configForm.thinking_enabled ? 'bg-purple-600' : 'bg-gray-600'}`}
                        >
                          <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${configForm.thinking_enabled ? 'left-4' : 'left-0.5'}`} />
                        </button>
                        <span className="text-xs text-gray-500">{configForm.thinking_enabled ? 'Enabled' : 'Disabled'}</span>
                      </div>
                      {configForm.thinking_enabled && (
                        <div className="flex items-center gap-4">
                          <label className="text-xs text-gray-400 w-24 shrink-0">Budget Tokens</label>
                          <input
                            type="number" min="1024" max="100000"
                            value={configForm.thinking_budget_tokens}
                            onChange={e => setConfigForm(f => ({ ...f, thinking_budget_tokens: parseInt(e.target.value) || 4000 }))}
                            className="flex-1 bg-gray-700 rounded px-2 py-1 text-sm border border-gray-600 focus:outline-none focus:border-emerald-500"
                          />
                        </div>
                      )}
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => handleConfigure(model.id)}
                          className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium"
                        >
                          Save Config
                        </button>
                        <button
                          onClick={() => setConfiguringId(null)}
                          className="px-3 py-1.5 hover:bg-gray-700 rounded-lg text-xs"
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
            {provider.error && <span className="text-xs text-red-400">Scan error: {provider.error}</span>}
            {!provider.error && <span className="text-xs text-gray-500">{provider.models.length} models</span>}
          </div>
          {provider.error ? (
            <div className="text-xs text-red-400 bg-red-900/20 rounded-lg px-3 py-2">Failed to fetch models: {provider.error}</div>
          ) : (
            <div className="space-y-1">
              {provider.models.map(model => {
                const on = isEnabled(provider, model.id)
                const busy = toggling.has(`${provider.provider_key}:${model.id}`)
                return (
                  <div key={model.id} className="flex items-center gap-3 px-3 py-2 bg-gray-800/30 rounded-lg hover:bg-gray-800/50 transition-colors">
                    <button
                      onClick={() => handleToggle(provider, model.id, !on)}
                      disabled={busy}
                      className={`w-9 h-5 rounded-full transition-colors relative shrink-0 ${busy ? 'opacity-50' : ''} ${on ? 'bg-emerald-600' : 'bg-gray-600'}`}
                    >
                      <div className={`w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 transition-all ${on ? 'left-4' : 'left-0.5'}`} />
                    </button>
                    <div className="flex-1 min-w-0">
                      <div className={`text-sm font-medium truncate ${on ? 'text-emerald-400' : 'text-gray-500'}`}>{model.name}</div>
                      <div className="text-xs text-gray-500 truncate">{model.id}</div>
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
      <p className="text-xs text-gray-500">Existing keys cannot be read — only overwritten. Leave blank to keep current value.</p>
      {(envStatus?.available || []).map(key => (
        <div key={key}>
          <label className="flex items-center gap-2 text-sm mb-1">
            <span>{envLabels[key] || key}</span>
            {envStatus?.configured.includes(key) && <span className="text-xs text-emerald-400">(configured)</span>}
          </label>
          <input
            type={isPasswordField(key) ? 'password' : 'text'}
            placeholder={envStatus?.configured.includes(key) ? '********' : isPasswordField(key) ? 'Enter...' : 'https://your-lnbits.com'}
            value={updates[key] || ''}
            onChange={e => setUpdates(u => ({ ...u, [key]: e.target.value }))}
            className="w-full bg-gray-800 rounded-lg px-3 py-2 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500"
          />
        </div>
      ))}
      <button
        onClick={handleSave}
        disabled={saving || Object.keys(updates).length === 0}
        className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium"
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
  const [newPlan, setNewPlan] = useState({ name: '', price_sats: 0, duration_days: 30, token_limit: '' })
  const [editingPlan, setEditingPlan] = useState<number | null>(null)
  const [editPlanData, setEditPlanData] = useState({ name: '', price_sats: 0, duration_days: 30, token_limit: '' })
  const [limitsPlan, setLimitsPlan] = useState<number | null>(null)
  const [limits, setLimits] = useState<PlanModelLimit[]>([])
  const [limitValues, setLimitValues] = useState<Record<number, string>>({})
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
      data.forEach(l => { vals[l.model_id] = l.token_limit?.toString() || '' })
      setLimitValues(vals)
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
      })
      setNewPlan({ name: '', price_sats: 0, duration_days: 30, token_limit: '' })
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

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-gray-400" /></div>

  return (
    <div className="space-y-6">
      {error && <div className="bg-red-900/30 border border-red-800 rounded-lg px-3 py-2 text-sm text-red-300">{error}</div>}

      {/* Plans */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-400">Plans</h3>
          <button
            onClick={() => setCreating(!creating)}
            className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm flex items-center gap-1"
          >
            <Plus className="w-3.5 h-3.5" /> New Plan
          </button>
        </div>

        {creating && (
          <div className="bg-gray-800/40 rounded-lg p-4 mb-3 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500">Name</label>
                <input
                  value={newPlan.name}
                  onChange={e => setNewPlan(p => ({ ...p, name: e.target.value }))}
                  placeholder="e.g. Pro"
                  className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">Price (sats)</label>
                <input
                  type="number"
                  value={newPlan.price_sats}
                  onChange={e => setNewPlan(p => ({ ...p, price_sats: parseInt(e.target.value) || 0 }))}
                  className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">Duration (days)</label>
                <input
                  type="number"
                  value={newPlan.duration_days}
                  onChange={e => setNewPlan(p => ({ ...p, duration_days: parseInt(e.target.value) || 0 }))}
                  className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500">Global token limit (optional)</label>
                <input
                  type="number"
                  value={newPlan.token_limit}
                  onChange={e => setNewPlan(p => ({ ...p, token_limit: e.target.value }))}
                  placeholder="Unlimited"
                  className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={handleCreate} className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-sm">Create</button>
              <button onClick={() => setCreating(false)} className="px-4 py-1.5 hover:bg-gray-700 rounded text-sm">Cancel</button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {plans.map(plan => (
            <div key={plan.id}>
              {editingPlan === plan.id ? (
                <div className="bg-gray-800/40 rounded-lg p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-gray-500">Name</label>
                      <input
                        value={editPlanData.name}
                        onChange={e => setEditPlanData(d => ({ ...d, name: e.target.value }))}
                        className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500">Price (sats)</label>
                      <input
                        type="number"
                        value={editPlanData.price_sats}
                        onChange={e => setEditPlanData(d => ({ ...d, price_sats: parseInt(e.target.value) || 0 }))}
                        className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500">Duration (days)</label>
                      <input
                        type="number"
                        value={editPlanData.duration_days}
                        onChange={e => setEditPlanData(d => ({ ...d, duration_days: parseInt(e.target.value) || 0 }))}
                        className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-500">Global token limit (optional)</label>
                      <input
                        type="number"
                        value={editPlanData.token_limit}
                        onChange={e => setEditPlanData(d => ({ ...d, token_limit: e.target.value }))}
                        placeholder="Unlimited"
                        className="w-full bg-gray-800 rounded px-3 py-1.5 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500 mt-1"
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => handleUpdate(plan.id)} className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-sm">Save</button>
                    <button onClick={() => setEditingPlan(null)} className="px-4 py-1.5 hover:bg-gray-700 rounded text-sm">Cancel</button>
                  </div>
                </div>
              ) : (
                <div className="bg-gray-800/40 rounded-lg px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <span className="font-medium">{plan.name}</span>
                      <span className="text-xs text-emerald-400">{plan.price_sats === 0 ? 'Free' : `${plan.price_sats.toLocaleString()} sats`}</span>
                      <span className="text-xs text-gray-500">{plan.duration_days > 0 ? `${plan.duration_days}d` : 'Unlimited'}</span>
                      {plan.token_limit && <span className="text-xs text-gray-500">{plan.token_limit.toLocaleString()} tokens</span>}
                      {!plan.enabled && <span className="text-xs px-1.5 py-0.5 bg-red-600/30 text-red-400 rounded">Disabled</span>}
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => limitsPlan === plan.id ? setLimitsPlan(null) : loadLimits(plan.id)}
                        className={`p-1.5 rounded text-xs ${limitsPlan === plan.id ? 'bg-emerald-600/30 text-emerald-400' : 'hover:bg-gray-700 text-gray-500 hover:text-gray-300'}`}
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
                          })
                        }}
                        className="p-1.5 hover:bg-gray-700 rounded text-gray-500 hover:text-gray-300"
                      >
                        <Edit3 className="w-3.5 h-3.5" />
                      </button>
                      {plan.name !== 'Free' && (
                        <button
                          onClick={() => handleDelete(plan.id, plan.name)}
                          className="p-1.5 hover:bg-red-800/30 rounded text-gray-500 hover:text-red-400"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      )}
                    </div>
                  </div>

                  {limitsPlan === plan.id && (
                    <div className="mt-3 pt-3 border-t border-gray-700 space-y-2">
                      <p className="text-xs text-gray-500">Per-model token limits for <span className="text-emerald-400">{plan.name}</span>. Empty = no limit.</p>
                      {models.length === 0 ? (
                        <p className="text-xs text-gray-500">No enabled models</p>
                      ) : (
                        <>
                          {models.map(m => (
                            <div key={m.id} className="flex items-center gap-2">
                              <span className="text-xs text-gray-400 w-40 truncate">{m.name}</span>
                              <input
                                type="number"
                                value={limitValues[m.id] || ''}
                                onChange={e => setLimitValues(v => ({ ...v, [m.id]: e.target.value }))}
                                placeholder="Unlimited"
                                className="w-24 bg-gray-800 rounded px-2 py-1 text-xs border border-gray-700 focus:outline-none focus:border-emerald-500"
                              />
                            </div>
                          ))}
                          <button onClick={handleSetLimits} disabled={savingLimits} className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded text-sm disabled:opacity-50">
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
          <h3 className="text-sm font-semibold text-gray-400">Active Subscriptions</h3>
          <button
            onClick={handleClearExpired}
            className="px-3 py-1.5 bg-red-600/20 hover:bg-red-600/40 rounded-lg text-xs text-red-400 flex items-center gap-1"
          >
            <Trash2 className="w-3.5 h-3.5" /> Clear Expired
          </button>
        </div>
        {subs.length === 0 ? (
          <p className="text-xs text-gray-500">No subscriptions yet.</p>
        ) : (
          <div className="space-y-1">
            {subs.map(sub => {
              const daysLeft = sub.expires_at
                ? Math.max(0, Math.ceil((new Date(sub.expires_at).getTime() - Date.now()) / (1000 * 60 * 60 * 24)))
                : 0
              return (
                <div key={sub.id} className="flex items-center justify-between bg-gray-800/30 rounded-lg px-3 py-2">
                  <div>
                    <span className="text-sm">{sub.plan_name || `Plan #${sub.plan_id}`}</span>
                    <span className={`ml-2 text-xs px-1.5 py-0.5 rounded ${
                      sub.status === 'active' ? 'bg-emerald-600/30 text-emerald-400' :
                      sub.status === 'pending' ? 'bg-amber-600/30 text-amber-400' :
                      'bg-red-600/30 text-red-400'
                    }`}>{sub.status}</span>
                    <span className="text-xs text-gray-500 ml-2">User #{sub.user_id}</span>
                    {sub.expires_at && sub.status === 'active' && (
                      <span className="text-xs text-gray-500 ml-2">{daysLeft}d left</span>
                    )}
                  </div>
                  <div className="flex gap-1">
                    <button
                      onClick={() => handleSubStatus(sub.id, 'active')}
                      className="px-2 py-1 text-xs hover:bg-emerald-800/30 rounded text-gray-500 hover:text-emerald-400"
                    >Activate</button>
                    <button
                      onClick={() => handleSubStatus(sub.id, 'expired')}
                      className="px-2 py-1 text-xs hover:bg-red-800/30 rounded text-gray-500 hover:text-red-400"
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
