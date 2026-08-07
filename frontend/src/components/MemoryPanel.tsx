import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import type { MemoryStatus, LintFinding } from '../types'
import {
  Brain, X, RefreshCw, Zap, Layers, SearchCheck, Trash2, AlertTriangle,
  Loader2, FileText, ListChecks, CheckCircle2, History, Sparkles,
} from 'lucide-react'

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
    await run('del', () => api.memory.deleteAtom(atomId), 'Fact deleted')
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

  const isBusy = busy !== null
  const idle = !isBusy

  const primaryBtn = (onClick: () => void, label: string, icon: React.ReactNode, action?: string) => (
    <button
      onClick={onClick}
      disabled={isBusy}
      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-theme-accent hover:bg-theme-accent-hover text-theme-text transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {action === busy ? <Loader2 className="w-4 h-4 animate-spin" /> : icon} {label}
    </button>
  )

  const ghostBtn = (onClick: () => void, label: string, icon: React.ReactNode, action?: string) => (
    <button
      onClick={onClick}
      disabled={isBusy}
      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border border-theme-border-light text-theme-text-secondary hover:bg-theme-bg-elevated hover:text-theme-text transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {action === busy ? <Loader2 className="w-4 h-4 animate-spin" /> : icon} {label}
    </button>
  )

  const dangerBtn = (onClick: () => void, label: string, icon: React.ReactNode, action?: string) => (
    <button
      onClick={onClick}
      disabled={isBusy}
      className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-theme-danger/15 hover:bg-theme-danger/25 text-theme-danger-text border border-theme-danger/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {action === busy ? <Loader2 className="w-4 h-4 animate-spin" /> : icon} {label}
    </button>
  )

  const sectionTitle = (icon: React.ReactNode, label: string, count?: React.ReactNode) => (
    <h3 className="flex items-center gap-2 text-sm font-semibold text-theme-text-secondary mb-2.5">
      <span className="flex items-center justify-center w-6 h-6 rounded-md bg-theme-accent/15 text-theme-accent-text">
        {icon}
      </span>
      {label}
      {count !== undefined && (
        <span className="text-xs font-normal text-theme-muted">· {count}</span>
      )}
    </h3>
  )

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 backdrop-blur-[2px] flex items-center justify-center z-50 p-4">
      <div className="llm-modal bg-theme-bg-secondary rounded-2xl w-full max-w-3xl max-h-[88vh] flex flex-col border border-theme-border-light shadow-2xl overflow-hidden">
        {/* header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-theme-border bg-theme-bg-elevated/30">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-theme-accent/20 text-theme-accent-text">
            <Brain className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-theme-text leading-tight">Memory</h2>
            <p className="text-xs text-theme-muted truncate">
              Auto-captured from your chats · visible only to you · {currentUser.username}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-theme-bg-elevated text-theme-muted hover:text-theme-text transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* actions */}
        <div className="flex items-center gap-2 px-6 py-3 border-b border-theme-border flex-wrap">
          <button
            onClick={load}
            disabled={isBusy || loading}
            className="p-2 rounded-lg border border-theme-border-light text-theme-text-secondary hover:bg-theme-bg-elevated hover:text-theme-text transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          {primaryBtn(() => run('extract', () => api.memory.extract(), 'Turned the latest messages into memory'), 'Extract now', <Zap className="w-4 h-4" />, 'extract')}
          {ghostBtn(() => run('consolidate', () => api.memory.consolidate(), 'Consolidated (dedupe + persona updated)'), 'Consolidate', <Layers className="w-4 h-4" />, 'consolidate')}
          {ghostBtn(handleLint, 'Lint', <SearchCheck className="w-4 h-4" />, 'lint')}
          <div className="flex-1" />
          {dangerBtn(handleWipe, 'Wipe memory', <Trash2 className="w-4 h-4" />, 'wipe')}
        </div>

        {(error || notice) && (
          <div className={`px-6 py-2.5 text-sm flex items-center gap-2 border-b ${
            error ? 'text-theme-danger-text bg-theme-danger/10 border-theme-danger/20'
                  : 'text-theme-accent-text bg-theme-accent/10 border-theme-accent/20'
          }`}>
            {error ? <AlertTriangle className="w-4 h-4 shrink-0" /> : <CheckCircle2 className="w-4 h-4 shrink-0" />}
            {error || notice}
          </div>
        )}

        {/* body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {loading && !status ? (
            <div className="flex flex-col items-center justify-center py-16 text-theme-muted gap-3">
              <Loader2 className="w-8 h-8 animate-spin text-theme-accent-text" />
              <span className="text-sm">Loading your memory…</span>
            </div>
          ) : status && status.atoms_count === 0 && status.scenarios_count === 0 && status.inbox_pending === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center gap-3">
              <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-theme-accent/15 text-theme-accent-text">
                <Sparkles className="w-7 h-7" />
              </div>
              <div className="text-sm text-theme-text-secondary max-w-sm">
                Nothing stored yet. Say something like{' '}
                <span className="text-theme-accent-text font-medium">“hi, I'm axo”</span> in a chat —
                it's distilled into memory automatically right after the reply.
              </div>
            </div>
          ) : status && (
            <>
              {/* status pills */}
              <div className="flex flex-wrap gap-2">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-theme-accent/15 text-theme-accent-text border border-theme-accent/25">
                  <CheckCircle2 className="w-3.5 h-3.5" /> {status.confirmed_count}/{status.atoms_count} facts
                </span>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-theme-bg-elevated text-theme-text-secondary border border-theme-border-light">
                  <FileText className="w-3.5 h-3.5" /> {status.scenarios_count} scenarios
                </span>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-theme-bg-elevated text-theme-text-secondary border border-theme-border-light">
                  <Layers className="w-3.5 h-3.5" /> {status.pages_count} pages
                </span>
                {status.inbox_pending > 0 && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-theme-amber/15 text-theme-amber border border-theme-amber/30">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> {status.inbox_pending} waiting to be extracted
                  </span>
                )}
              </div>

              {/* working set */}
              {(status.persona.length > 0 || status.active.length > 0) && (
                <section>
                  {sectionTitle(<Brain className="w-4 h-4" />, 'Working set', 'always injected')}
                  <div className="grid gap-2 sm:grid-cols-2">
                    {status.persona.length > 0 && (
                      <div className="p-3.5 rounded-xl bg-theme-bg-elevated/50 border border-theme-border">
                        <div className="text-[11px] uppercase tracking-wider text-theme-muted mb-2">Persona</div>
                        <div className="space-y-1.5">
                          {status.persona.map((l, i) => (
                            <div key={i} className="text-sm text-theme-text pl-2.5 border-l-2 border-theme-accent/60">- {l}</div>
                          ))}
                        </div>
                      </div>
                    )}
                    {status.active.length > 0 && (
                      <div className="p-3.5 rounded-xl bg-theme-bg-elevated/50 border border-theme-border">
                        <div className="text-[11px] uppercase tracking-wider text-theme-muted mb-2">Active state</div>
                        <div className="space-y-1.5">
                          {status.active.map((l, i) => (
                            <div key={i} className="text-sm text-theme-text pl-2.5 border-l-2 border-theme-blue/60">- {l}</div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </section>
              )}

              {/* facts */}
              {status.atoms.length > 0 && (
                <section>
                  {sectionTitle(<ListChecks className="w-4 h-4" />, 'Facts', status.atoms.length)}
                  <div className="space-y-1.5">
                    {status.atoms.map((a) => (
                      <div key={a.id} className="group flex items-start gap-3 p-3 rounded-xl bg-theme-bg-elevated/50 border border-theme-border hover:border-theme-border-light transition-colors">
                        <div className={`mt-1 w-2 h-2 rounded-full shrink-0 ${
                          a.superseded ? 'bg-theme-muted' : a.confirmed ? 'bg-theme-accent-text' : 'bg-theme-amber'
                        }`} title={a.superseded ? 'superseded' : a.confirmed ? 'confirmed' : 'unconfirmed'} />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm text-theme-text leading-snug">
                            {a.text}
                            {a.superseded && <span className="ml-2 text-xs text-theme-muted">[superseded {a.superseded}]</span>}
                          </div>
                          <div className="text-xs text-theme-muted mt-1 flex flex-wrap items-center gap-1.5">
                            <span className="text-theme-subtle">{a.entity}</span>
                            {a.tags.map((t) => (
                              <span key={t} className="px-1.5 py-0.5 rounded bg-theme-bg-secondary border border-theme-border-light text-theme-text-secondary">#{t}</span>
                            ))}
                            <span className="px-1.5 py-0.5 rounded bg-theme-accent/10 text-theme-accent-text">{a.kind}</span>
                            {!a.confirmed && (
                              <span className="px-1.5 py-0.5 rounded bg-theme-amber/10 text-theme-amber">unconfirmed</span>
                            )}
                          </div>
                        </div>
                        {confirmDelete === a.id ? (
                          <div className="flex items-center gap-1.5 shrink-0">
                            <button onClick={() => handleDeleteAtom(a.id)} disabled={isBusy}
                              className="px-2.5 py-1.5 rounded-lg text-xs font-medium bg-theme-danger hover:bg-theme-danger-hover text-white transition-colors">
                              Delete
                            </button>
                            <button onClick={() => setConfirmDelete(null)}
                              className="px-2.5 py-1.5 rounded-lg text-xs border border-theme-border-light text-theme-text-secondary hover:bg-theme-bg-elevated transition-colors">
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => setConfirmDelete(a.id)}
                            className="opacity-0 group-hover:opacity-100 p-1.5 rounded-lg text-theme-muted hover:text-theme-danger-text hover:bg-theme-danger/10 transition-all"
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
                  {sectionTitle(<FileText className="w-4 h-4" />, 'Scenarios', status.scenarios.length)}
                  <div className="space-y-1.5">
                    {status.scenarios.map((s) => (
                      <div key={s.id} className="p-3.5 rounded-xl bg-theme-bg-elevated/50 border border-theme-border">
                        <div className="text-sm font-medium text-theme-text">{s.title}</div>
                        {s.summary && <div className="text-sm text-theme-text-secondary mt-1 leading-snug">{s.summary}</div>}
                        {s.tags.length > 0 && (
                          <div className="text-xs text-theme-muted mt-2 flex gap-1.5">
                            {s.tags.map((t) => (
                              <span key={t} className="px-1.5 py-0.5 rounded bg-theme-bg-secondary border border-theme-border-light">#{t}</span>
                            ))}
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
                  {sectionTitle(<SearchCheck className="w-4 h-4" />, 'Lint findings', findings.length)}
                  <div className="space-y-1.5">
                    {findings.map((f, i) => (
                      <div key={i} className={`p-3 rounded-xl border text-sm ${
                        f.severity === 'error'
                          ? 'border-theme-danger/30 bg-theme-danger/10 text-theme-danger-text'
                          : f.severity === 'warn'
                            ? 'border-theme-amber/30 bg-theme-amber/10 text-theme-amber'
                            : 'border-theme-border bg-theme-bg-elevated/50 text-theme-text-secondary'
                      }`}>
                        <span className="font-medium uppercase text-xs mr-1.5">[{f.severity}]</span>
                        {f.check}: {f.detail}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* log */}
              {status.log.length > 0 && (
                <section>
                  {sectionTitle(<History className="w-4 h-4" />, 'Activity log')}
                  <pre className="text-xs text-theme-text-secondary whitespace-pre-wrap bg-theme-bg-elevated/50 border border-theme-border rounded-xl p-4 max-h-44 overflow-y-auto leading-relaxed">
                    {status.log.join('\n')}
                  </pre>
                </section>
              )}
            </>
          )}
        </div>

        {/* wipe confirm */}
        {confirmWipe && (
          <div className="border-t border-theme-danger/30 bg-theme-danger/10 px-6 py-4 flex items-center gap-3">
            <AlertTriangle className="w-6 h-6 text-theme-danger-text shrink-0" />
            <div className="flex-1 text-sm text-theme-text">
              Permanently delete <b>all</b> of your memory, including raw transcripts? This cannot be undone.
            </div>
            <button onClick={handleWipe} disabled={isBusy}
              className="px-3.5 py-2 rounded-lg text-sm font-medium bg-theme-danger hover:bg-theme-danger-hover text-white transition-colors disabled:opacity-50">
              {busy === 'wipe' ? 'Wiping…' : 'Yes, wipe it'}
            </button>
            <button onClick={() => setConfirmWipe(false)} disabled={isBusy}
              className="px-3.5 py-2 rounded-lg text-sm border border-theme-border-light text-theme-text-secondary hover:bg-theme-bg-elevated transition-colors">
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

export default MemoryPanel
