import React, { useState } from 'react'
import { Bot, Loader2, UserPlus } from 'lucide-react'
import { api } from '../api'
import type { AuthResponse, AuthStatus } from '../types'

interface Props {
  authStatus: AuthStatus
  onDone: (resp: AuthResponse) => void
}

export default function LoginPage({ authStatus, onDone }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
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
    <div className="h-screen flex items-center justify-center bg-gray-950">
      <div className="w-full max-w-sm mx-4">
        <div className="text-center mb-8">
          <Bot className="w-14 h-14 text-emerald-400 mx-auto mb-3" />
          <h1 className="text-2xl font-bold text-gray-100">LLMDash</h1>
          <p className="text-sm text-gray-500 mt-1">
            {mode === 'login' ? 'Sign in to continue' : 'Create an account'}
          </p>
        </div>
        <form onSubmit={handleSubmit} className="bg-gray-900 rounded-xl border border-gray-800 p-6 space-y-4">
          {error && (
            <div className="bg-red-900/30 border border-red-800 rounded-lg px-3 py-2 text-sm text-red-300">{error}</div>
          )}
          <div>
            <label className="text-sm text-gray-400 mb-1 block">Username</label>
            <input
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoFocus
              className="w-full bg-gray-800 rounded-lg px-3 py-2 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500"
            />
          </div>
          <div>
            <label className="text-sm text-gray-400 mb-1 block">Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-gray-800 rounded-lg px-3 py-2 text-sm border border-gray-700 focus:outline-none focus:border-emerald-500"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 rounded-lg text-sm font-medium"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin inline" /> : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
          {authStatus.registration_enabled && (
            <button
              type="button"
              onClick={() => { setMode(m => m === 'login' ? 'register' : 'login'); setError('') }}
              className="w-full py-2 text-sm text-gray-400 hover:text-emerald-400 transition-colors flex items-center justify-center gap-1"
            >
              <UserPlus className="w-3.5 h-3.5" />
              {mode === 'login' ? 'Create new account' : 'Back to login'}
            </button>
          )}
        </form>
      </div>
    </div>
  )
}
