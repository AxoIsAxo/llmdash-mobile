import React, { useState } from 'react'
import { Bot, Loader2 } from 'lucide-react'
import { api } from '../api'
import type { AuthResponse } from '../types'

interface Props {
  onDone: (resp: AuthResponse) => void
}

export default function SetupWizard({ onDone }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!username.trim()) { setError('Username required'); return }
    if (password.length < 4) { setError('Password must be at least 4 characters'); return }
    if (password !== confirm) { setError('Passwords do not match'); return }
    setLoading(true)
    try {
      const resp = await api.auth.setup(username.trim(), password)
      api.setToken(resp.token)
      onDone(resp)
    } catch (e: any) {
      setError(e.message || 'Setup failed')
    }
    setLoading(false)
  }

  return (
    <div className="h-screen flex items-center justify-center bg-theme-bg">
      <div className="w-full max-w-[var(--theme-auth-max-width)] mx-4">
        <div className="text-center mb-8">
          <Bot className="w-14 h-14 text-theme-accent-text mx-auto mb-3" />
          <h1 className="text-2xl font-bold text-theme-text">LLMDash Setup</h1>
          <p className="text-sm text-theme-muted mt-1">Create your owner account</p>
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
          <div>
            <label className="text-sm text-theme-subtle mb-1 block">Confirm Password</label>
            <input
              type="password"
              value={confirm}
              onChange={e => setConfirm(e.target.value)}
              className="w-full bg-theme-bg-elevated rounded-lg px-3 py-2 text-sm border border-theme-border-light focus:outline-none focus:border-theme-focus-ring"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover rounded-lg text-sm font-medium"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin inline" /> : 'Create Account'}
          </button>
        </form>
      </div>
    </div>
  )
}
