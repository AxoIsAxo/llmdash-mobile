import React, { useEffect, useState } from 'react'
import { Loader2, RefreshCw, Download, Check, X, Store } from 'lucide-react'
import { api } from '../api'
import type { MarketplaceOverview, MarketplaceCatalogEntry, MarketplaceInstalled } from '../types'

export default function MarketplaceTab({ currentUser }: { currentUser: { entitlements?: Record<string, boolean> } }) {
  const [data, setData] = useState<MarketplaceOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [installUrl, setInstallUrl] = useState('')
  const [installing, setInstalling] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const marketplaceEntitled = currentUser.entitlements?.skill_marketplace !== false

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.marketplace.list())
    } catch (e: any) {
      setError(e.message || 'Failed to load marketplace')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const handleInstall = async (url?: string) => {
    const target = (url ?? installUrl).trim()
    if (!target) return
    setInstalling(true)
    setError('')
    try {
      await api.marketplace.install(target)
      setInstallUrl('')
      await load()
    } catch (e: any) {
      setError(e.message || 'Install failed')
    }
    setInstalling(false)
  }

  const handleApprove = async (name: string) => {
    setBusy(name)
    setError('')
    try {
      await api.marketplace.approve(name)
      await load()
    } catch (e: any) { setError(e.message) }
    setBusy(null)
  }

  const handleUninstall = async (name: string) => {
    if (!confirm(`Uninstall skill "${name}"? This removes the code and revokes access for all users instantly.`)) return
    setBusy(name)
    setError('')
    try {
      await api.marketplace.uninstall(name)
      await load()
    } catch (e: any) { setError(e.message) }
    setBusy(null)
  }

  const installedByUrl: Record<string, MarketplaceInstalled> = {}
  for (const i of data?.installed || []) installedByUrl[i.source_url] = i

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-xs text-theme-muted">
          Install community skills from a git repository. Installed skills are <b>not visible to users</b> until you
          approve them (review gate). Only you and admins can install, approve or uninstall.
        </p>
        <button onClick={load} className="p-1.5 hover:bg-theme-bg-hover rounded-lg text-theme-subtle hover:text-theme-text" title="Reload">
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      {!marketplaceEntitled && (
        <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">
          Your plan does not include the skill marketplace.
        </div>
      )}
      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>}

      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>
      ) : (
        <>
          {/* Install from URL */}
          <div className="bg-theme-bg-elevated/40 rounded-lg p-4 space-y-2">
            <h3 className="text-sm font-semibold text-theme-subtle">Install from URL</h3>
            <p className="text-xs text-theme-muted">
              The repo must contain a <code className="font-mono">manifest.json</code> at its root with name, version,
              author, description, category, scopes (only scopes builtins already use), entitlement (optional) and an
              <code className="font-mono"> entry</code> module defining the <code className="font-mono">Skill</code> subclass.
            </p>
            <div className="flex gap-2">
              <input
                value={installUrl}
                onChange={e => setInstallUrl(e.target.value)}
                placeholder="https://codeberg.org/user/my-skill.git"
                className="flex-1 bg-theme-bg-elevated rounded px-3 py-1.5 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
              />
              <button
                onClick={() => handleInstall()}
                disabled={installing || !installUrl.trim() || !marketplaceEntitled}
                className="px-4 py-1.5 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted rounded text-sm flex items-center gap-1"
              >
                {installing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />} Install
              </button>
            </div>
          </div>

          {/* Catalog */}
          <div>
            <h3 className="text-sm font-semibold text-theme-subtle mb-2">Catalog</h3>
            {(data?.catalog.entries || []).length === 0 ? (
              <p className="text-xs text-theme-muted">
                The bundled catalog is empty. Add entries to <code className="font-mono">backend/app/marketplace_catalog.json</code> or
                install directly from a URL above.
              </p>
            ) : (
              <div className="space-y-2">
                {data!.catalog.entries.map((entry: MarketplaceCatalogEntry) => {
                  const installed = installedByUrl[entry.install_url]
                  return (
                    <div key={entry.name} className="flex items-start gap-3 bg-theme-bg-elevated/30 rounded-lg px-3 py-2.5">
                      <Store className="w-4 h-4 text-theme-accent-text mt-0.5 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-mono text-sm">{entry.name}</span>
                          <span className="text-[10px] text-theme-muted">v{entry.version}</span>
                          <span className="text-[10px] text-theme-muted">by {entry.author}</span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-theme-bg-active text-theme-muted">{entry.category}</span>
                          {entry.scopes.map(s => <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-theme-accent/20 text-theme-accent-text">{s}</span>)}
                        </div>
                        <p className="text-xs text-theme-text-secondary mt-1">{entry.description}</p>
                      </div>
                      {installed ? (
                        <span className="text-xs text-theme-accent-text shrink-0 flex items-center gap-1">
                          <Check className="w-3.5 h-3.5" /> Installed{installed.approved ? '' : ' (unapproved)'}
                        </span>
                      ) : (
                        <button
                          onClick={() => handleInstall(entry.install_url)}
                          disabled={installing || !marketplaceEntitled}
                          className="px-3 py-1 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted rounded text-xs shrink-0"
                        >
                          Install
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Installed */}
          <div>
            <h3 className="text-sm font-semibold text-theme-subtle mb-2">Installed</h3>
            {(data?.installed || []).length === 0 ? (
              <p className="text-xs text-theme-muted">Nothing installed yet.</p>
            ) : (
              <div className="space-y-2">
                {data!.installed.map(skill => (
                  <div key={skill.name} className="flex items-start gap-3 bg-theme-bg-elevated/30 rounded-lg px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono text-sm">{skill.name}</span>
                        <span className="text-[10px] text-theme-muted">v{skill.version}</span>
                        <span className="text-[10px] text-theme-muted">by {skill.author}</span>
                        {skill.scopes.map(s => <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-theme-accent/20 text-theme-accent-text">{s}</span>)}
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${skill.approved ? 'bg-theme-accent/30 text-theme-accent-text' : 'bg-theme-amber/30 text-theme-amber'}`}>
                          {skill.approved ? 'Approved' : 'Pending review'}
                        </span>
                      </div>
                      <p className="text-xs text-theme-text-secondary mt-1">{skill.description}</p>
                      <p className="text-[10px] text-theme-muted mt-1 font-mono truncate">{skill.source_url}</p>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      {!skill.approved && (
                        <button
                          onClick={() => handleApprove(skill.name)}
                          disabled={busy === skill.name || !marketplaceEntitled}
                          className="px-3 py-1 bg-theme-accent hover:bg-theme-accent-hover disabled:opacity-50 rounded text-xs flex items-center gap-1"
                        >
                          <Check className="w-3 h-3" /> Approve
                        </button>
                      )}
                      <button
                        onClick={() => handleUninstall(skill.name)}
                        disabled={busy === skill.name || !marketplaceEntitled}
                        className="px-3 py-1 bg-theme-danger/20 hover:bg-theme-danger/40 disabled:opacity-50 rounded text-xs text-theme-danger-text flex items-center gap-1"
                      >
                        <X className="w-3 h-3" /> Uninstall
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
