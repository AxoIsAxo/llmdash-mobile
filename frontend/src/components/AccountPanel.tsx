import React, { useState } from 'react'
import { api } from '../api'
import type { User } from '../types'
import { X, UserRound, LogIn, Loader2, CheckCircle2, Link2 } from 'lucide-react'
import SubscriptionSection from './SubscriptionPage'

interface Props {
  currentUser: User
  onClose: () => void
  onRefreshUser: () => void
}

function AccountPanel({ currentUser, onClose, onRefreshUser }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConnect = async () => {
    setBusy(true)
    setError(null)
    try {
      const { url } = await api.auth.extrovertStart('link')
      window.location.href = url
    } catch (e: any) {
      setError(e.message || 'Could not start Extrovert linking')
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 backdrop-blur-[2px] flex items-center justify-center z-50 p-4">
      <div className="llm-modal bg-theme-bg-secondary rounded-2xl w-full max-w-2xl max-h-[88vh] flex flex-col border border-theme-border-light shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-6 py-4 border-b border-theme-border bg-theme-bg-elevated/30">
          <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-theme-accent/20 text-theme-accent-text">
            <UserRound className="w-5 h-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-theme-text leading-tight">Account</h2>
            <p className="text-xs text-theme-muted truncate">{currentUser.username}</p>
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-theme-bg-elevated text-theme-muted hover:text-theme-text transition-colors" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6">
          {error && <div className="text-sm text-theme-danger-text bg-theme-danger/10 border border-theme-danger/20 rounded-lg px-3 py-2">{error}</div>}

          <div className="flex items-center justify-between p-3 rounded-xl bg-theme-bg-elevated/50 border border-theme-border">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-theme-muted">Role</span>
              <span className="px-1.5 py-0.5 rounded bg-theme-accent/10 text-theme-accent-text text-xs">{currentUser.role}</span>
            </div>
            <div className="text-xs text-theme-text-secondary">
              {currentUser.token_usage.toLocaleString()} / {currentUser.token_limit ? currentUser.token_limit.toLocaleString() : '∞'} tokens
            </div>
          </div>

          <div className="p-4 rounded-xl bg-theme-bg-elevated/50 border border-theme-border">
            <div className="flex items-center gap-2 text-sm font-medium text-theme-text mb-1">
              <Link2 className="w-4 h-4 text-theme-accent-text" />
              Extrovert login
            </div>
            <p className="text-xs text-theme-muted mb-3">
              {currentUser.extrovert_linked
                ? 'This account is linked to your Extrovert identity — you can sign in with Extrovert.'
                : 'Link this account to your Extrovert identity. It will be renamed to your Extrovert username (@…) and you can sign in with Extrovert from then on.'}
            </p>
            {currentUser.extrovert_linked ? (
              <div className="flex items-center gap-2 text-sm text-theme-accent-text">
                <CheckCircle2 className="w-4 h-4" /> Linked to Extrovert
              </div>
            ) : (
              <button
                onClick={handleConnect}
                disabled={busy}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-theme-accent hover:bg-theme-accent-hover text-theme-text transition-colors disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
                Connect with Extrovert
              </button>
            )}
          </div>
          <SubscriptionSection currentUser={currentUser} />
        </div>
      </div>
    </div>
  )
}

export default AccountPanel
