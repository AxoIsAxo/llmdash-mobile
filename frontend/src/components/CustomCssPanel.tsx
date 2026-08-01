import React, { useState, useEffect, useRef } from 'react'
import { X, Eye, RotateCcw, Maximize2, Minimize2, History, Loader2 } from 'lucide-react'
import { api } from '../api'
import { DEFAULT_CSS } from '../css-preset'
import type { ThemeHistoryEntry } from '../types'

const STYLE_ID = 'llmdash-user-css'

function injectCss(css: string) {
  const el = document.getElementById(STYLE_ID) as HTMLStyleElement | null
  if (el) {
    el.textContent = css || DEFAULT_CSS
    ;(window as any).__syncPwaTheme?.()
  }
}

interface Props {
  currentCss: string
  onClose: () => void
  onSaved: (css: string) => void
}

export default function CustomCssPanel({ currentCss, onClose, onSaved }: Props) {
  const [css, setCss] = useState(currentCss || DEFAULT_CSS)
  const [fullscreen, setFullscreen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [presets, setPresets] = useState<string[]>([])
  const [applyingPreset, setApplyingPreset] = useState(false)
  const [presetError, setPresetError] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<ThemeHistoryEntry[]>([])
  const [restoringId, setRestoringId] = useState<number | null>(null)

  useEffect(() => {
    setCss(currentCss || DEFAULT_CSS)
  }, [currentCss])

  useEffect(() => {
    api.auth.theme.get().then(t => setPresets(t.presets || [])).catch(() => {})
  }, [])

  const loadHistory = async () => {
    try {
      const rows = await api.auth.theme.history()
      setHistory(rows || [])
    } catch {}
  }

  const handlePreview = () => {
    injectCss(css)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.auth.css.save(css)
      injectCss(css)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      onSaved(css)
    } catch (e: any) {
      alert('Failed to save: ' + e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!confirm('Reset to default CSS? This will overwrite your custom styles.')) return
    setCss(DEFAULT_CSS)
    try {
      await api.auth.css.save(DEFAULT_CSS)
      injectCss(DEFAULT_CSS)
      onSaved(DEFAULT_CSS)
    } catch (e: any) {
      alert('Failed to save: ' + e.message)
    }
  }

  const handleApplyPreset = async (preset: string) => {
    setApplyingPreset(true)
    setPresetError('')
    try {
      const res = await api.auth.theme.reset(preset)
      setCss(res.css)
      injectCss(res.css)
      onSaved(res.css)
    } catch (e: any) {
      setPresetError(e.message || 'Failed to apply preset')
    } finally {
      setApplyingPreset(false)
    }
  }

  const handleRestore = async (id: number) => {
    setRestoringId(id)
    try {
      const res = await api.auth.theme.restore(id)
      setCss(res.css)
      injectCss(res.css)
      onSaved(res.css)
      setHistoryOpen(false)
    } catch (e: any) {
      alert('Failed to restore: ' + e.message)
    } finally {
      setRestoringId(null)
    }
  }

  const previewText = css.slice(0, 60).replace(/\s+/g, ' ')

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className={`llm-modal bg-theme-bg-secondary rounded-2xl border border-theme-border-light flex flex-col ${
          fullscreen ? 'fixed inset-4 w-auto h-auto max-w-none max-h-none' : 'w-full max-w-3xl max-h-[85vh]'
        }`}
        onClick={e => e.stopPropagation()}
      >
        <div className="p-4 border-b border-theme-border flex items-center justify-between shrink-0">
          <h2 className="text-lg font-semibold">Custom CSS & Theme</h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { setHistoryOpen(!historyOpen); if (!historyOpen) loadHistory() }}
              className="p-1.5 hover:bg-theme-bg-hover rounded-lg text-theme-subtle hover:text-theme-text"
              title="Theme history (undo)"
            >
              <History className="w-4 h-4" />
            </button>
            <button
              onClick={() => setFullscreen(!fullscreen)}
              className="p-1.5 hover:bg-theme-bg-hover rounded-lg text-theme-subtle hover:text-theme-text"
              title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
            >
              {fullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
            </button>
            <button onClick={onClose} className="px-3 py-1.5 hover:bg-theme-bg-hover rounded-lg text-sm">Close</button>
          </div>
        </div>

        {historyOpen && (
          <div className="shrink-0 max-h-40 overflow-y-auto border-b border-theme-border bg-theme-bg-elevated/40 px-4 py-3 space-y-1">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-xs font-semibold text-theme-subtle uppercase tracking-wider">Theme History</h3>
              <button onClick={() => setHistoryOpen(false)} className="text-xs text-theme-muted hover:text-theme-text">Close</button>
            </div>
            {history.length === 0 && <p className="text-xs text-theme-muted">No saved theme versions yet.</p>}
            {history.map(h => (
              <div key={h.id} className="flex items-center justify-between gap-2">
                <span className="text-xs text-theme-muted font-mono">
                  {new Date(h.created_at).toLocaleString()}
                  {h.spec ? ` — ${h.spec.preset || 'custom'}` : ''}
                </span>
                <button
                  onClick={() => handleRestore(h.id)}
                  disabled={restoringId === h.id}
                  className="px-2 py-0.5 bg-theme-accent hover:bg-theme-accent-hover rounded text-xs disabled:opacity-50"
                >
                  {restoringId === h.id ? 'Restoring...' : 'Restore'}
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <p className="text-xs text-theme-muted">
            Customize the appearance of LLMDash. Changes apply immediately on preview or save.
            The default CSS is used as fallback.
          </p>

          {presets.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-theme-muted">Theme presets:</span>
              {presets.map(p => (
                <button
                  key={p}
                  onClick={() => handleApplyPreset(p)}
                  disabled={applyingPreset}
                  className="px-3 py-1 bg-theme-bg-hover hover:bg-theme-bg-active rounded-lg text-xs capitalize transition-colors disabled:opacity-50"
                >
                  {applyingPreset ? 'Applying...' : p}
                </button>
              ))}
            </div>
          )}
          {presetError && <p className="text-xs text-theme-danger-text">{presetError}</p>}

          <textarea
            ref={textareaRef}
            value={css}
            onChange={e => { setCss(e.target.value); setSaved(false) }}
            className="w-full bg-theme-bg-elevated rounded-lg px-4 py-3 text-sm font-mono border border-theme-border-light focus:outline-none focus:border-theme-focus-ring resize-none"
            style={{ minHeight: fullscreen ? 'calc(100vh - 240px)' : '400px' }}
            spellCheck={false}
          />
          <div className="flex items-center gap-2">
            <button
              onClick={handlePreview}
              className="px-4 py-1.5 bg-theme-bg-hover hover:bg-theme-bg-active rounded-lg text-sm flex items-center gap-1.5"
            >
              <Eye className="w-4 h-4" /> Preview
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-1.5 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted rounded-lg text-sm flex items-center gap-1.5"
            >
              {saving ? 'Saving...' : saved ? 'Saved!' : 'Save'}
            </button>
            <button
              onClick={handleReset}
              className="px-4 py-1.5 hover:bg-theme-bg-hover rounded-lg text-sm flex items-center gap-1.5 text-theme-subtle"
            >
              <RotateCcw className="w-4 h-4" /> Reset to default
            </button>
            <span className="text-xs text-theme-subtle ml-auto">{css.length} chars</span>
          </div>
        </div>
      </div>
    </div>
  )
}
