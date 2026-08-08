import React, { useState } from 'react'
import { Bot, Loader2, UserPlus, LogIn } from 'lucide-react'
import { api } from '../api'
import { startExtrovertOAuth } from '../oauth'
import type { AuthResponse, AuthStatus } from '../types'

interface Props {
  authStatus: AuthStatus
  onDone: (resp: AuthResponse) => void
}

export default function LoginPage({ authStatus, onDone }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [extrovertLoading, setExtrovertLoading] = useState(false)

  const handleExtrovert = async () => {
    setExtrovertLoading(true)
    setError('')
    try {
      const { url } = await api.auth.extrovertStart()
      startExtrovertOAuth(url)
    } catch (e: any) {
      setError(e.message || 'Could not start Extrovert login')
      setExtrovertLoading(false)
    }
  }
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<'login' | 'register'>('login')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!username.trim() || !password) { setError('Fill in all fields'); return }
    setLoading(true)
    try {
      let resp: AuthResponse
      if (mode === 'register') {
        resp = await api.auth.register(username.trim(), password)
      } else {
        resp = await api.auth.login(username.trim(), password)
      }
      api.setToken(resp.token)
      onDone(resp)
    } catch (e: any) {
      setError(e.message || 'Failed')
    }
    setLoading(false)
  }

  return (
    <div className="h-screen flex items-center justify-center bg-theme-bg">
      <div className="w-full max-w-[var(--theme-auth-max-width)] mx-4">
        <div className="text-center mb-8">
          <Bot className="w-14 h-14 text-theme-accent-text mx-auto mb-3" />
          <h1 className="text-2xl font-bold text-theme-text">LLMDash</h1>
          <p className="text-sm text-theme-muted mt-1">
            {mode === 'login' ? 'Sign in to continue' : 'Create an account'}
          </p>
        </div>
        <form onSubmit={handleSubmit} className="bg-theme-bg-secondary rounded-xl border border-theme-border p-6 space-y-4">
          {error && (
            <div className="bg-theme-danger/30 border border-theme-danger rounded-lg px-3 py-2 text-sm text-theme-danger-text">{error}</div>
          )}
          <div>
            <label className="text-sm text-theme-subtle mb-1 block">Username</label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
              className="w-full bg-theme-bg-elevated rounded-lg px-3 py-2 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
            />
          </div>
          <div>
            <label className="text-sm text-theme-subtle mb-1 block">Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-theme-bg-elevated rounded-lg px-3 py-2 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover rounded-lg text-sm font-medium"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin inline" /> : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
          {authStatus.registration_enabled && (
            <button
              type="button"
              onClick={() => { setMode(m => m === 'login' ? 'register' : 'login'); setError('') }}
              className="w-full py-2 text-sm text-theme-subtle hover:text-theme-accent-text transition-colors flex items-center justify-center gap-1"
            >
              <UserPlus className="w-3.5 h-3.5" />
              {mode === 'login' ? 'Create new account' : 'Back to login'}
            </button>
          )}
          {authStatus.extrovert_enabled && (
            <>
              <div className="flex items-center gap-2 my-3 text-xs text-theme-muted">
                <span className="flex-1 h-px bg-theme-border-light" />
                or
                <span className="flex-1 h-px bg-theme-border-light" />
              </div>
              <button
                type="button"
                onClick={handleExtrovert}
                disabled={extrovertLoading}
                className="w-full py-2 rounded-lg text-sm font-medium border border-theme-border-light text-theme-text-secondary hover:bg-theme-bg-elevated hover:text-theme-text transition-colors flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {extrovertLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4 text-theme-accent-text" />}
                Continue with Extrovert
              </button>
            </>
          )}
        </form>
      </div>
    </div>
  )
}
