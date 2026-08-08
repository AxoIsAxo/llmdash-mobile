import React, { useState, useEffect } from 'react'
import { Plus, Trash2, Loader2, X, Check, GitBranch, Lock, Unlock, Activity } from 'lucide-react'
import { api } from '../api'
import type { GitRepo, GitInfo, GitAuditEntry } from '../types'

interface Props {
  isAdmin?: boolean
}

export default function GitPanel({ isAdmin = false }: Props) {
  const [repos, setRepos] = useState<GitRepo[]>([])
  const [info, setInfo] = useState<GitInfo | null>(null)
  const [audit, setAudit] = useState<GitAuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    name: '', clone_url: '', access: 'read', auth_type: 'none',
    credential: '', default_branch: 'main', pr_preferred: true, global_scope: false,
  })

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [r, i, a] = await Promise.all([api.git.repos.list(), api.git.info(), api.git.audit()])
      setRepos(r); setInfo(i); setAudit(a)
    } catch (e: any) { setError(e.message) }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const handleCreate = async () => {
    if (!form.name.trim() || !form.clone_url.trim()) { setError('Name and clone URL are required'); return }
    setSaving(true)
    setError('')
    try {
      await api.git.repos.create(form)
      setForm({ name: '', clone_url: '', access: 'read', auth_type: 'none', credential: '', default_branch: 'main', pr_preferred: true, global_scope: false })
      setShowForm(false)
      await load()
    } catch (e: any) { setError(e.message) }
    setSaving(false)
  }

  const handleToggle = async (repo: GitRepo, key: 'enabled' | 'pr_preferred', value: boolean) => {
    setError('')
    try {
      await api.git.repos.update(repo.id, { [key]: value })
      await load()
    } catch (e: any) { setError(e.message) }
  }

  const handleAccess = async (repo: GitRepo, access: 'read' | 'write') => {
    setError('')
    try {
      await api.git.repos.update(repo.id, { access })
      await load()
    } catch (e: any) { setError(e.message) }
  }

  const handleDelete = async (repo: GitRepo) => {
    if (!window.confirm(`Remove '${repo.name}' from your git allowlist?`)) return
    setError('')
    try {
      await api.git.repos.delete(repo.id)
      await load()
    } catch (e: any) { setError(e.message) }
  }

  const handleClearCredential = async (repo: GitRepo) => {
    if (!window.confirm(`Remove the stored credential for '${repo.name}'?`)) return
    setError('')
    try {
      await api.git.repos.update(repo.id, { clear_credential: true })
      await load()
    } catch (e: any) { setError(e.message) }
  }

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>

  return (
    <div className="space-y-6">
      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>}

      <div>
        <h3 className="text-sm font-semibold text-theme-subtle mb-1 flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-theme-accent-text" /> Git Repositories
        </h3>
        <p className="text-xs text-theme-muted mb-3">
          The AI can clone, commit, and push only in repositories on your allowlist. Every commit is authored by{' '}
          <span className="text-theme-accent-text">{info ? `${info.bot_name} <${info.bot_email}>` : 'the bot identity'}</span> —
          never your git identity. Read-only by default; grant write access per repo. All actions are audit-logged.
          Customize the author via <code className="text-theme-accent-text/80">GIT_BOT_NAME</code> /{' '}
          <code className="text-theme-accent-text/80">GIT_BOT_EMAIL</code> in the Admin Panel → API Keys.
        </p>

        {repos.length === 0 && !showForm && (
          <div className="bg-theme-bg-elevated/40 rounded-lg p-6 text-center">
            <p className="text-sm text-theme-muted">No repositories configured yet.</p>
            <button
              onClick={() => setShowForm(true)}
              className="mt-3 px-4 py-2 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-sm font-medium inline-flex items-center gap-1.5"
            >
              <Plus className="w-4 h-4" /> Add Repository
            </button>
          </div>
        )}

        {repos.map(repo => (
          <div key={repo.id} className="bg-theme-bg-elevated/40 rounded-lg p-4 mb-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`font-medium text-sm ${repo.enabled ? '' : 'text-theme-muted line-through'}`}>{repo.name}</span>
                  <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${repo.access === 'write' ? 'bg-theme-accent/20 text-theme-accent-text' : 'bg-theme-bg-hover text-theme-muted'}`}>
                    {repo.access === 'write' ? <span className="inline-flex items-center gap-1"><Unlock className="w-3 h-3" /> write</span> : <span className="inline-flex items-center gap-1"><Lock className="w-3 h-3" /> read</span>}
                  </span>
                  {repo.scope === 'global' && <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-theme-purple/10 text-theme-purple">shared</span>}
                  {repo.credential_set && <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-theme-blue-text/10 text-theme-blue-text">{repo.auth_type}</span>}
                  {repo.credential_set && (
                    <button
                      onClick={() => handleClearCredential(repo)}
                      title="Remove the stored credential"
                      className="text-[10px] text-theme-muted hover:text-theme-danger-text underline underline-offset-2"
                    >
                      clear credential
                    </button>
                  )}
                </div>
                <div className="text-xs text-theme-muted mt-1 truncate font-mono">{repo.host}/{repo.clone_url.split(/[\/:]/).slice(-2).join('/')}</div>
                <div className="text-xs text-theme-muted mt-0.5">
                  default branch: <code>{repo.default_branch}</code>
                  {repo.pr_preferred && ' · PRs preferred over direct pushes'}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleAccess(repo, repo.access === 'write' ? 'read' : 'write')}
                  title={repo.access === 'write' ? 'Revoke write access' : 'Grant write access'}
                  className={`p-1.5 rounded-lg border text-xs ${repo.access === 'write' ? 'border-theme-danger/40 text-theme-danger-text hover:bg-theme-danger/20' : 'border-theme-border-light/30 text-theme-muted hover:bg-theme-bg-hover'}`}
                >
                  {repo.access === 'write' ? <Lock className="w-3.5 h-3.5" /> : <Unlock className="w-3.5 h-3.5" />}
                </button>
                <button
                  onClick={() => handleToggle(repo, 'pr_preferred', !repo.pr_preferred)}
                  title={repo.pr_preferred ? 'Allow direct pushes to the default branch' : 'Prefer pull requests over direct pushes'}
                  className="p-1.5 rounded-lg border border-theme-border-light/30 text-theme-muted hover:bg-theme-bg-hover"
                >
                  <GitBranch className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => handleToggle(repo, 'enabled', !repo.enabled)}
                  className={`p-1.5 rounded-lg border ${repo.enabled ? 'border-theme-border-light/30 text-theme-muted hover:bg-theme-bg-hover' : 'border-theme-accent/40 text-theme-accent-text'}`}
                >
                  {repo.enabled ? <Check className="w-3.5 h-3.5" /> : <X className="w-3.5 h-3.5" />}
                </button>
                <button onClick={() => handleDelete(repo)} className="p-1.5 rounded-lg border border-theme-danger/40 text-theme-danger-text hover:bg-theme-danger/20">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          </div>
        ))}

        {!showForm && repos.length > 0 && (
          <button
            onClick={() => setShowForm(true)}
            className="w-full py-2 border border-dashed border-theme-border-light/50 rounded-lg text-sm text-theme-muted hover:text-theme-text hover:border-theme-accent/40 inline-flex items-center justify-center gap-1.5"
          >
            <Plus className="w-4 h-4" /> Add Repository
          </button>
        )}

        {showForm && (
          <div className="bg-theme-bg-elevated/40 rounded-lg p-4 space-y-3 mt-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="block">
                <span className="text-xs text-theme-muted">Name (how the AI refers to it)</span>
                <input
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="my-repo"
                  className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
                />
              </label>
              <label className="block">
                <span className="text-xs text-theme-muted">Clone URL</span>
                <input
                  value={form.clone_url}
                  onChange={e => setForm(f => ({ ...f, clone_url: e.target.value }))}
                  placeholder="git@github.com:owner/repo.git  or  https://codeberg.org/owner/repo.git"
                  className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30 font-mono"
                />
              </label>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <label className="block">
                <span className="text-xs text-theme-muted">Access</span>
                <select
                  value={form.access}
                  onChange={e => setForm(f => ({ ...f, access: e.target.value }))}
                  className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
                >
                  <option value="read">read — clone/inspect only</option>
                  <option value="write">write — commit & push</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-theme-muted">Auth</span>
                <select
                  value={form.auth_type}
                  onChange={e => setForm(f => ({ ...f, auth_type: e.target.value, credential: '' }))}
                  className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
                >
                  <option value="none">none — public repo</option>
                  <option value="ssh_key">ssh deploy key</option>
                  <option value="token">https bot token</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-theme-muted">Default branch</span>
                <input
                  value={form.default_branch}
                  onChange={e => setForm(f => ({ ...f, default_branch: e.target.value }))}
                  placeholder="main"
                  className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30"
                />
              </label>
            </div>
            {form.auth_type !== 'none' && (
              <label className="block">
                <span className="text-xs text-theme-muted">
                  {form.auth_type === 'ssh_key' ? 'Deploy key (private key PEM)' : 'Bot token (optionally "user:token")'}
                </span>
                <textarea
                  value={form.credential}
                  onChange={e => setForm(f => ({ ...f, credential: e.target.value }))}
                  rows={form.auth_type === 'ssh_key' ? 4 : 1}
                  placeholder={form.auth_type === 'ssh_key' ? '-----BEGIN OPENSSH PRIVATE KEY-----...' : 'glpat-xxx or user:token'}
                  className="mt-1 w-full bg-theme-bg-elevated rounded px-2 py-1.5 text-sm border border-theme-border-light/30 font-mono"
                />
                <span className="text-[11px] text-theme-muted">Stored server-side, scoped to this repo, never shown in chat or returned by the API.</span>
              </label>
            )}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.pr_preferred}
                onChange={e => setForm(f => ({ ...f, pr_preferred: e.target.checked }))}
                className="accent-theme-accent"
              />
              Prefer pull requests over direct pushes to the default branch
            </label>
            {isAdmin && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.global_scope}
                  onChange={e => setForm(f => ({ ...f, global_scope: e.target.checked }))}
                  className="accent-theme-accent"
                />
                Share with every user (shared repository)
              </label>
            )}
            <div className="flex gap-2">
              <button
                onClick={handleCreate}
                disabled={saving}
                className="px-4 py-2 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted rounded-lg text-sm font-medium"
              >
                {saving ? 'Adding...' : 'Add Repository'}
              </button>
              <button onClick={() => setShowForm(false)} className="px-4 py-2 hover:bg-theme-bg-hover rounded-lg text-sm">Cancel</button>
            </div>
          </div>
        )}
      </div>

      {isAdmin && (
        <div>
          <h3 className="text-sm font-semibold text-theme-subtle mb-2 flex items-center gap-2">
            <Activity className="w-4 h-4 text-theme-accent-text" /> Recent Git Actions
          </h3>
          {audit.length === 0 ? (
            <p className="text-xs text-theme-muted">No git actions logged yet.</p>
          ) : (
            <div className="bg-theme-bg-elevated/40 rounded-lg divide-y divide-theme-border/40 max-h-56 overflow-y-auto">
              {audit.map(entry => (
                <div key={entry.id} className="px-3 py-2 flex items-center gap-2 text-xs">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${entry.success ? 'bg-theme-accent' : 'bg-theme-danger'}`} />
                  <span className="font-mono text-theme-accent-text/90 shrink-0">{entry.action}</span>
                  <span className="text-theme-muted truncate flex-1">{entry.detail || ''}</span>
                  <span className="text-theme-subtle shrink-0">{new Date(entry.created_at).toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
