import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import type { MemoryStatus, LintFinding } from '../types'
import { Brain, X, RefreshCw, Zap, Layers, SearchCheck, Trash2, AlertTriangle, Loader2, FileText, ListChecks } from 'lucide-react'

interface Props {
  currentUser: { username: string }
  onClose: () => void
}

function MemoryPanel({ currentUser, onClose }: Props) {
  const [status, setStatus] = useState<MemoryStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null) // which action is running
  const [findings, setFindings] = useState<LintFinding[] | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [confirmWipe, setConfirmWipe] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setStatus(await api.memory.status())
    } catch (e: any) {
      setError(e.message || 'Failed to load memory')
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const run = async (action: string, fn: () => Promise<unknown>, doneMsg: string) => {
    setBusy(action)
    setError(null)
    setNotice(null)
    try {
      await fn()
      if (doneMsg) setNotice(doneMsg)
      await load()
    } catch (e: any) {
      setError(e.message || 'Operation failed')
    }
    setBusy(null)
  }

  const handleDeleteAtom = async (atomId: string) => {
    await run('del', () => api.memory.deleteAtom(atomId), 'Atom deleted')
    setConfirmDelete(null)
  }

  const handleWipe = async () => {
    await run('wipe', () => api.memory.reset(), 'Memory wiped')
    setConfirmWipe(false)
  }

  const handleLint = async () => {
    setBusy('lint')
    setError(null)
    try {
      const res = await api.memory.lint()
      setFindings(res.findings)
    } catch (e: any) {
      setError(e.message || 'Lint failed')
    }
    setBusy(null)
  }

  const btn = (onClick: () => void, label: string, icon: React.ReactNode, disabled = false, danger = false) => (
    <button
      onClick={onClick}
      disabled={disabled || busy !== null}
      className={`px-3 py-2 rounded-lg text-sm flex items-center gap-1.5 disabled:opacity-50 ${
        danger ? 'bg-theme-danger hover:bg-theme-danger-hover text-white' : 'bg-theme-accent hover:bg-theme-accent-hover text-theme-accent-text'
      }`}
    >
      {icon} {label}
    </button>
  )

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 flex items-center justify-center z-50">
      <div className="llm-modal bg-theme-bg-secondary rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col border border-theme-border-light shadow-2xl">
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-theme-border">
          <Brain className="w-5 h-5 text-theme-accent-text" />
          <h2 className="text-lg font-semibold flex-1">Memory — {currentUser.username}</h2>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-theme-bg-elevated text-theme-muted" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* actions */}
        <div className="flex items-center gap-2 px-5 py-3 border-b border-theme-border flex-wrap">
          {btn(load, 'Refresh', <RefreshCw className="w-4 h-4" />, loading)}
          {btn(() => run('extract', () => api.memory.extract(), 'Turned the latest messages into memory'), 'Extract now', <Zap className="w-4 h-4" />)}
          {btn(() => run('consolidate', () => api.memory.consolidate(), 'Consolidated (dedupe + persona updated)'), 'Consolidate', <Layers className="w-4 h-4" />)}
          {btn(handleLint, 'Lint', <SearchCheck className="w-4 h-4" />)}
          {btn(handleWipe, 'Wipe my memory', <Trash2 className="w-4 h-4" />, false, true)}
          {busy && <span className="text-xs text-theme-muted flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> {busy}…</span>}
        </div>

        {(error || notice) && (
          <div className={`px-5 py-2 text-sm ${error ? 'text-theme-danger' : 'text-theme-accent-text'}`}>
            {error || notice}
          </div>
        )}

        {/* body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {loading && !status ? (
            <div className="flex items-center gap-2 text-theme-muted text-sm"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>
          ) : status && status.atoms_count === 0 && status.scenarios_count === 0 && status.inbox_pending === 0 ? (
            <div className="text-theme-muted text-sm">
              Nothing stored yet. Say something like <span className="text-theme-accent-text">“hi, I'm axo”</span> in a chat and it will
              be distilled here automatically after the reply.
            </div>
          ) : status && (
            <>
              {/* status line */}
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="px-2 py-1 rounded-full bg-theme-bg-elevated border border-theme-border-light">
                  {status.confirmed_count}/{status.atoms_count} atoms
                </span>
                <span className="px-2 py-1 rounded-full bg-theme-bg-elevated border border-theme-border-light">
                  {status.scenarios_count} scenarios
                </span>
                <span className="px-2 py-1 rounded-full bg-theme-bg-elevated border border-theme-border-light">
                  {status.pages_count} pages
                </span>
                <span className="px-2 py-1 rounded-full bg-theme-bg-elevated border border-theme-border-light">
                  {status.inbox_pending} waiting to be extracted
                </span>
              </div>

              {/* working set */}
              {(status.persona.length > 0 || status.active.length > 0) && (
                <section>
                  <h3 className="text-sm font-semibold text-theme-accent-text mb-2 flex items-center gap-1.5">
                    <Brain className="w-4 h-4" /> Working set (always injected)
                  </h3>
                  <div className="space-y-2 text-sm">
                    {status.persona.length > 0 && (
                      <div className="p-3 rounded-lg bg-theme-bg-elevated border border-theme-border">
                        <div className="text-xs text-theme-muted mb-1">Persona</div>
                        {status.persona.map((l, i) => <div key={i} className="pl-2 border-l-2 border-theme-accent-text/40">- {l}</div>)}
                      </div>
                    )}
                    {status.active.length > 0 && (
                      <div className="p-3 rounded-lg bg-theme-bg-elevated border border-theme-border">
                        <div className="text-xs text-theme-muted mb-1">Active state</div>
                        {status.active.map((l, i) => <div key={i} className="pl-2 border-l-2 border-theme-accent-text/40">- {l}</div>)}
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* atoms */}
              {status.atoms.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-theme-accent-text mb-2 flex items-center gap-1.5">
                    <ListChecks className="w-4 h-4" /> Facts ({status.atoms.length})
                  </h3>
                  <div className="space-y-1.5">
                    {status.atoms.map((a) => (
                      <div key={a.id} className="flex items-start gap-2 p-2.5 rounded-lg bg-theme-bg-elevated border border-theme-border group">
                        <div className="flex-1 min-w-0">
                          <div className="text-sm">
                            {a.text}
                            {a.superseded && <span className="ml-2 text-xs text-theme-muted">[superseded {a.superseded}]</span>}
                          </div>
                          <div className="text-xs text-theme-muted mt-0.5 flex flex-wrap gap-1.5">
                            <span>{a.entity}</span>
                            {a.tags.map((t) => <span key={t} className="px-1 rounded bg-theme-bg-secondary">#{t}</span>)}
                            <span>{a.kind}</span>
                            {!a.confirmed && <span className="text-theme-amber">unconfirmed</span>}
                          </div>
                        </div>
                        {confirmDelete === a.id ? (
                          <div className="flex items-center gap-1.5 text-xs">
                            <button onClick={() => handleDeleteAtom(a.id)} className="px-2 py-1 rounded bg-theme-danger text-white">Delete</button>
                            <button onClick={() => setConfirmDelete(null)} className="px-2 py-1 rounded bg-theme-bg-secondary text-theme-muted">Cancel</button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setConfirmDelete(a.id)}
                            className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-theme-danger/20 text-theme-muted hover:text-theme-danger transition-opacity"
                            title="Delete this fact"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* scenarios */}
              {status.scenarios.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-theme-accent-text mb-2 flex items-center gap-1.5">
                    <FileText className="w-4 h-4" /> Scenarios ({status.scenarios.length})
                  </h3>
                  <div className="space-y-1.5">
                    {status.scenarios.map((s) => (
                      <div key={s.id} className="p-2.5 rounded-lg bg-theme-bg-elevated border border-theme-border">
                        <div className="text-sm font-medium">{s.title}</div>
                        {s.summary && <div className="text-sm text-theme-muted mt-0.5">{s.summary}</div>}
                        {s.tags.length > 0 && (
                          <div className="text-xs text-theme-muted mt-1 flex gap-1.5">
                            {s.tags.map((t) => <span key={t} className="px-1 rounded bg-theme-bg-secondary">#{t}</span>)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* lint findings */}
              {findings && (
                <section>
                  <h3 className="text-sm font-semibold text-theme-accent-text mb-2">Lint findings</h3>
                  <div className="space-y-1 text-sm">
                    {findings.map((f, i) => (
                      <div key={i} className={`p-2 rounded-lg border ${
                        f.severity === 'error' ? 'border-theme-danger text-theme-danger'
                        : f.severity === 'warn' ? 'border-theme-amber text-theme-amber'
                        : 'border-theme-border text-theme-muted'
                      }`}>
                        [{f.severity}] {f.check}: {f.detail}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* log */}
              {status.log.length > 0 && (
                <section>
                  <h3 className="text-sm font-semibold text-theme-accent-text mb-2">Activity log</h3>
                  <pre className="text-xs text-theme-muted whitespace-pre-wrap bg-theme-bg-elevated border border-theme-border rounded-lg p-3 max-h-40 overflow-y-auto">
                    {status.log.join('\n')}
                  </pre>
                </section>
              )}
            </>
          )}
        </div>

        {/* wipe confirm */}
        {confirmWipe && (
          <div className="border-t border-theme-border px-5 py-3 flex items-center gap-3 text-sm">
            <AlertTriangle className="w-5 h-5 text-theme-danger" />
            <span className="flex-1">Permanently delete <b>all</b> of your memory (including raw transcripts)? This cannot be undone.</span>
            <button onClick={handleWipe} disabled={busy !== null} className="px-3 py-2 rounded-lg bg-theme-danger hover:bg-theme-danger-hover text-white text-sm">Yes, wipe it</button>
            <button onClick={() => setConfirmWipe(false)} className="px-3 py-2 rounded-lg bg-theme-bg-elevated text-theme-muted text-sm">Cancel</button>
          </div>
        )}
      </div>
    </div>
  )
}

export default MemoryPanel
