import React, { useEffect, useState } from 'react'
import { Loader2, RefreshCw, ChevronDown, ChevronUp, Shield } from 'lucide-react'
import { api } from '../api'
import type { SkillInfo } from '../types'

const SCOPE_COLORS: Record<string, string> = {
  web: 'text-theme-blue-text bg-theme-blue/20',
  git: 'text-theme-accent-text bg-theme-accent/20',
  files: 'text-theme-amber bg-theme-amber/20',
  sandbox: 'text-theme-danger-text bg-theme-danger/20',
  theme: 'text-theme-purple bg-theme-purple/20',
  render: 'text-theme-accent-dim bg-theme-accent/20',
  proton: 'text-theme-blue-text bg-theme-blue/20',
}

function ScopeBadge({ scope }: { scope: string }) {
  const cls = SCOPE_COLORS[scope] || 'text-theme-muted bg-theme-bg-active'
  return <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${cls}`}>{scope}</span>
}

export default function SkillsPanel({ embedded }: { embedded?: boolean }) {
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Record<string, string>>({}) // name -> config JSON text
  const [saving, setSaving] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.skills.list()
      setSkills(data.sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)))
    } catch (e: any) {
      setError(e.message || 'Failed to load skills')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const toggleConfig = (skill: SkillInfo) => {
    setExpanded(prev => {
      const next = { ...prev }
      if (next[skill.name] !== undefined) {
        delete next[skill.name]
      } else {
        next[skill.name] = JSON.stringify(skill.config || {}, null, 2)
      }
      return next
    })
  }

  const toggleSkill = async (skill: SkillInfo) => {
    setSaving(skill.name)
    try {
      await api.skills.enable(skill.name, !skill.user_enabled)
      setSkills(prev => prev.map(s => s.name === skill.name ? { ...s, user_enabled: !s.user_enabled } : s))
    } catch (e: any) {
      alert('Failed to update: ' + e.message)
    }
    setSaving(null)
  }

  const saveConfig = async (skill: SkillInfo) => {
    const raw = expanded[skill.name]
    if (raw === undefined) return
    let parsed: Record<string, unknown>
    try {
      parsed = raw.trim() ? JSON.parse(raw) : {}
      if (typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('config must be a JSON object')
    } catch (e: any) {
      alert('Invalid JSON: ' + e.message)
      return
    }
    setSaving(skill.name)
    try {
      await api.skills.config(skill.name, parsed)
      setSkills(prev => prev.map(s => s.name === skill.name ? { ...s, config: parsed } : s))
      setExpanded(prev => { const next = { ...prev }; delete next[skill.name]; return next })
    } catch (e: any) {
      alert('Failed to save config: ' + e.message)
    }
    setSaving(null)
  }

  const groups: { label: string; items: SkillInfo[] }[] = []
  for (const source of ['builtin', 'marketplace', 'user']) {
    const items = skills.filter(s => s.source === source)
    if (items.length > 0) groups.push({ label: source === 'builtin' ? 'Built-in skills' : source === 'marketplace' ? 'Marketplace skills' : 'User skills', items })
  }

  return (
    <div className={embedded ? 'flex-1 flex flex-col min-h-0 overflow-hidden' : 'p-4'}>
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-theme-muted">
          Skills the AI can use in your chats. Disable any you don't want — the model only sees your enabled set.
        </p>
        <button onClick={load} className="p-1.5 hover:bg-theme-bg-hover rounded-lg text-theme-subtle hover:text-theme-text" title="Reload">
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      {error && <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text mb-3">{error}</div>}
      {loading ? (
        <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-theme-subtle" /></div>
      ) : groups.length === 0 ? (
        <p className="text-xs text-theme-muted">No skills available.</p>
      ) : (
        <div className="space-y-4">
          {groups.map(g => (
            <div key={g.label}>
              <h3 className="text-xs font-semibold text-theme-subtle uppercase tracking-wider mb-1.5">{g.label}</h3>
              <div className="space-y-2">
                {g.items.map(skill => (
                  <div key={skill.name} className={`rounded-lg border transition-colors ${
                    skill.user_enabled ? 'border-theme-border-light bg-theme-bg-elevated/30' : 'border-theme-border-light/50 bg-theme-bg-elevated/10 opacity-70'
                  }`}>
                    <div className="flex items-start gap-3 px-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-mono text-sm">{skill.name}</span>
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-theme-bg-active text-theme-muted">{skill.category}</span>
                          <span className="text-[10px] text-theme-muted">v{skill.version}</span>
                          {skill.entitlement && <span className="text-[10px] text-theme-muted" title={`Requires the '${skill.entitlement}' plan feature`}><Shield className="w-3 h-3 inline mr-0.5" />{skill.entitlement}</span>}
                          {skill.scopes.map(sc => <ScopeBadge key={sc} scope={sc} />)}
                        </div>
                        <p className="text-xs text-theme-text-secondary mt-1 line-clamp-2">{skill.description}</p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          type="button"
                          role="switch"
                          aria-checked={skill.user_enabled}
                          onClick={() => toggleSkill(skill)}
                          disabled={saving === skill.name}
                          className={`relative w-9 h-5 rounded-full transition-colors ${
                            skill.user_enabled ? 'bg-theme-accent' : 'bg-theme-bg-active border border-theme-border-light'
                          } disabled:opacity-50`}
                          title={skill.user_enabled ? 'Enabled — click to disable for your chats' : 'Disabled — click to enable'}
                        >
                          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${skill.user_enabled ? 'translate-x-4' : ''}`} />
                        </button>
                        <button
                          onClick={() => toggleConfig(skill)}
                          className="p-1 hover:bg-theme-bg-hover rounded text-theme-muted hover:text-theme-text"
                          title="Custom config"
                        >
                          {expanded[skill.name] !== undefined ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                        </button>
                      </div>
                    </div>
                    {expanded[skill.name] !== undefined && (
                      <div className="px-3 pb-3 pt-1 border-t border-theme-border-light/50 space-y-2">
                        <p className="text-xs text-theme-muted">Custom config (JSON object) — passed to the skill when the model calls it.</p>
                        <textarea
                          value={expanded[skill.name]}
                          onChange={e => setExpanded(prev => ({ ...prev, [skill.name]: e.target.value }))}
                          spellCheck={false}
                          className="w-full bg-theme-bg-elevated rounded-lg px-3 py-2 text-xs font-mono border border-theme-border-light focus:outline-none focus:border-theme-focus-ring resize-y"
                          style={{ minHeight: '90px' }}
                        />
                        <div className="flex gap-2">
                          <button onClick={() => saveConfig(skill)} disabled={saving === skill.name} className="px-3 py-1 bg-theme-accent hover:bg-theme-accent-hover rounded text-xs disabled:opacity-50">
                            {saving === skill.name ? 'Saving...' : 'Save Config'}
                          </button>
                          <button onClick={() => setExpanded(prev => { const n = { ...prev }; delete n[skill.name]; return n })} className="px-3 py-1 hover:bg-theme-bg-active rounded text-xs">
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
