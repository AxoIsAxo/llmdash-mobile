import React, { useState, useEffect, useRef, useCallback } from 'react'
import { api } from './api'
import type { ModelConfig, Conversation, Message, StreamEvent, ToolCall, User, AuthStatus } from './types'

interface SidePanel {
  html: string
  filename: string
  format: string
}
import {
  Send, Plus, Key, MessageSquare, Trash2, ChevronLeft,
  ChevronRight, Wrench, Bot, Loader2, Terminal, Globe, FileText, Eye, Search,
  Copy, Check, RefreshCw, Square, ChevronUp, ChevronDown, Download,
  Shield, LogOut, Settings, Minus, CreditCard
} from 'lucide-react'
import MarkdownRenderer from './components/MarkdownRenderer'
import SetupWizard from './components/SetupWizard'
import LoginPage from './components/LoginPage'
import AdminPanel from './components/AdminPanel'
import SubscriptionPage from './components/SubscriptionPage'

function App() {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null)
  const [currentUser, setCurrentUser] = useState<User | null>(null)
  const [authLoading, setAuthLoading] = useState(true)

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeConv, setActiveConv] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [models, setModels] = useState<ModelConfig[]>([])
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [showSidebar, setShowSidebar] = useState(true)
  const [showAdmin, setShowAdmin] = useState(false)
  const [showSubscription, setShowSubscription] = useState(false)
  const [showModelPickerFooter, setShowModelPickerFooter] = useState(false)
  const [showModelPickerEmpty, setShowModelPickerEmpty] = useState(false)
  const closeModelPickers = () => { setShowModelPickerFooter(false); setShowModelPickerEmpty(false) }
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const [branchSiblings, setBranchSiblings] = useState<Record<string, number[]>>({})
  const [branchConvToKey, setBranchConvToKey] = useState<Record<number, string>>({})
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [executingTools, setExecutingTools] = useState<Set<string>>(new Set())
  const [sidePanel, setSidePanel] = useState<SidePanel | null>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    try { setConversations(await api.conversations.list()) } catch {}
  }, [])

  const loadModels = useCallback(async () => {
    try {
      const all = await api.models.list()
      setModels(all)
      if (all.length > 0) {
        setSelectedModelId(prev => {
          if (prev && all.find(m => m.id === prev)) return prev
          return all[0].id
        })
      }
    } catch {}
  }, [])

  const checkAuth = useCallback(async () => {
    const token = api.getToken()
    if (!token) {
      try {
        const status = await api.auth.status()
        setAuthStatus(status)
      } catch (e) {
        setAuthStatus({ needs_setup: false, registration_enabled: false })
      }
      setAuthLoading(false)
      return
    }
    try {
      const user = await api.auth.me()
      setCurrentUser(user)
    } catch {
      api.setToken(null)
      const status = await api.auth.status()
      setAuthStatus(status)
    }
    setAuthLoading(false)
  }, [])

  useEffect(() => { checkAuth() }, [checkAuth])

  useEffect(() => {
    if (currentUser) {
      loadConversations()
      loadModels()
    }
  }, [currentUser, loadConversations, loadModels])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (!showModelPickerFooter && !showModelPickerEmpty) return
    const handler = () => closeModelPickers()
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [showModelPickerFooter, showModelPickerEmpty])

  const handleAuthDone = (user: User) => {
    setCurrentUser(user)
    setAuthStatus(null)
  }

  const handleLogout = async () => {
    api.setToken(null)
    setCurrentUser(null)
    setConversations([])
    setActiveConv(null)
    setMessages([])
    try {
      const status = await api.auth.status()
      setAuthStatus(status)
    } catch {
      setAuthStatus({ needs_setup: false, registration_enabled: false })
    }
  }

  const createConv = async () => {
    setSidePanel(null)
    const modelId = selectedModelId || undefined
    try {
      const conv = await api.conversations.create({ title: 'New Chat', model_id: modelId })
      setConversations(prev => [conv, ...prev])
      setActiveConv(conv)
      setMessages([])
    } catch { /* ignore */ }
  }

  const deleteConv = async (id: number) => {
    try {
      await api.conversations.delete(id)
      setConversations(prev => prev.filter(c => c.id !== id))
      if (activeConv?.id === id) { setActiveConv(null); setMessages([]) }
    } catch { /* ignore */ }
  }

  const selectConv = async (conv: Conversation) => {
    setActiveConv(conv)
    setSidePanel(null)
    try { setMessages(await api.conversations.messages(conv.id)) } catch { setMessages([]) }
  }

  const handleSend = async () => {
    if (!input.trim() || streaming) return
    setSidePanel(null)
    const modelId = selectedModelId
    if (!modelId) { alert('No enabled model configured. Ask an admin to set one up.'); return }

    let conv = activeConv
    if (!conv) {
      try {
        conv = await api.conversations.create({ title: 'New Chat', model_id: modelId })
        setConversations(prev => [conv!, ...prev])
        setActiveConv(conv!)
      } catch { return }
    }

    const userMsg: Message = {
      id: Date.now(), role: 'user', content: input,
      tool_calls_json: null, tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
    }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setStreaming(true)

    const controller = new AbortController()
    setAbortController(controller)

    try {
      let assistantContent = ''
      const toolCalls: ToolCall[] = []

      for await (const event of api.chat.send(conv.id, userMsg.content || '', modelId, controller.signal)) {
          if (event.type === 'content_delta') {
            assistantContent += (event.content || '')
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'assistant' && last.id === (conv?.id || 0) * -1) {
                return [...prev.slice(0, -1), { ...last, content: assistantContent }]
              }
              return [...prev, {
                id: (conv?.id || 0) * -1, role: 'assistant' as const,
                content: assistantContent, tool_calls_json: null,
                tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'content') {
            assistantContent = event.content || ''
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'assistant' && last.id === (conv?.id || 0) * -1) {
                return [...prev.slice(0, -1), { ...last, content: assistantContent }]
              }
              return [...prev, {
                id: (conv?.id || 0) * -1, role: 'assistant' as const,
                content: assistantContent, tool_calls_json: null,
                tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'tool_calls') {
            toolCalls.length = 0
            toolCalls.push(...(event.tool_calls || []))
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'assistant' && last.id === (conv?.id || 0) * -1) {
                return [...prev.slice(0, -1), { ...last, content: assistantContent, tool_calls_json: event.tool_calls || null, id: Date.now() }]
              }
              return [...prev, {
                id: Date.now(), role: 'assistant' as const,
                content: event.content || '', tool_calls_json: event.tool_calls || null,
                tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'tool_start') {
            if (event.id) setExecutingTools(prev => new Set(prev).add(event.id!))
          } else if (event.type === 'tool_result') {
            if (event.id) setExecutingTools(prev => { const next = new Set(prev); next.delete(event.id!); return next })
            if (event.name === 'edit_document' && event.result) {
              const htmlMatch = event.result.match(/HTML_RENDER:([A-Za-z0-9+/=]+)/)
              if (htmlMatch) {
                const tc = toolCalls.find(t => t.id === event.id)
                if (tc) {
                  setSidePanel({
                    html: atob(htmlMatch[1]),
                    filename: (tc.arguments.filename as string) || 'document',
                    format: (tc.arguments.format as string) || '',
                  })
                }
              }
            }
            setMessages(prev => [...prev, {
              id: Date.now(), role: 'tool' as const, content: event.result || '',
              tool_calls_json: null, tool_call_id: event.id || null,
              tool_name: event.name || null, created_at: new Date().toISOString()
            }])
          } else if (event.type === 'error') {
            setMessages(prev => [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: `Error: ${event.error}`, tool_calls_json: null,
              tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
            }])
          }
        }

        setMessages(prev => {
          if (assistantContent && prev[prev.length - 1]?.content !== assistantContent) {
            return [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: assistantContent, tool_calls_json: toolCalls.length ? toolCalls : null,
              tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
            }]
          }
          return prev
        })
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        setMessages(prev => [...prev, {
          id: Date.now(), role: 'assistant' as const,
          content: `Error: ${e.message}`, tool_calls_json: null,
          tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
        }])
      }
    } finally {
      setExecutingTools(new Set())
      setStreaming(false)
      setAbortController(null)
      loadConversations()
      if (currentUser) {
        try { setCurrentUser(await api.auth.me()) } catch {}
      }
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const handleCancel = () => { abortController?.abort(); setSidePanel(null) }

  const handleCopy = async (content: string, id: number) => {
    try { await navigator.clipboard.writeText(content); setCopiedId(id); setTimeout(() => setCopiedId(null), 2000) } catch {}
  }

  const handleSwitchBranch = async (convId: number) => {
    const conv = conversations.find(c => c.id === convId)
    if (conv) { setActiveConv(conv); try { setMessages(await api.conversations.messages(convId)) } catch { setMessages([]) } }
  }

  const handleRegenerate = async (msgIndex: number) => {
    if (!activeConv || streaming) return
    const msg = messages[msgIndex]
    if (!msg || msg.role !== 'user') return
    const content = msg.content || ''
    const modelId = selectedModelId
    if (!modelId) { alert('No enabled model configured.'); return }

    try {
      const branch = await api.conversations.branch(activeConv.id, msgIndex)
      const key = `${activeConv.id}:${msgIndex}`
      setBranchSiblings(prev => {
        const existing = prev[key] || [activeConv.id]
        if (existing.includes(branch.id)) return prev
        return { ...prev, [key]: [...existing, branch.id] }
      })
      setBranchConvToKey(prev => ({ ...prev, [branch.id]: key }))
      setConversations(prev => [branch, ...prev])
      setActiveConv(branch)
      const branchMsgs = await api.conversations.messages(branch.id)
      setMessages(branchMsgs)
      setInput('')
      setStreaming(true)

      const controller = new AbortController()
      setAbortController(controller)

      try {
        let assistantContent = ''
        const toolCalls: ToolCall[] = []
        for await (const event of api.chat.send(branch.id, content, modelId, controller.signal)) {
          if (event.type === 'content_delta') {
            assistantContent += (event.content || '')
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'assistant' && last.id === branch.id * -1) {
                return [...prev.slice(0, -1), { ...last, content: assistantContent }]
              }
              return [...prev, {
                id: branch.id * -1, role: 'assistant' as const,
                content: assistantContent, tool_calls_json: null,
                tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'content') {
            assistantContent = event.content || ''
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'assistant' && last.id === branch.id * -1) {
                return [...prev.slice(0, -1), { ...last, content: assistantContent }]
              }
              return [...prev, {
                id: branch.id * -1, role: 'assistant' as const,
                content: assistantContent, tool_calls_json: null,
                tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'tool_calls') {
            toolCalls.length = 0
            toolCalls.push(...(event.tool_calls || []))
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last?.role === 'assistant' && last.id === branch.id * -1) {
                return [...prev.slice(0, -1), { ...last, content: assistantContent, tool_calls_json: event.tool_calls || null, id: Date.now() }]
              }
              return [...prev, {
                id: Date.now(), role: 'assistant' as const,
                content: event.content || '', tool_calls_json: event.tool_calls || null,
                tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'tool_start') {
            if (event.id) setExecutingTools(prev => new Set(prev).add(event.id!))
          } else if (event.type === 'tool_result') {
            if (event.id) setExecutingTools(prev => { const next = new Set(prev); next.delete(event.id!); return next })
            if (event.name === 'edit_document' && event.result) {
              const htmlMatch = event.result.match(/HTML_RENDER:([A-Za-z0-9+/=]+)/)
              if (htmlMatch) {
                const tc = toolCalls.find(t => t.id === event.id)
                if (tc) {
                  setSidePanel({
                    html: atob(htmlMatch[1]),
                    filename: (tc.arguments.filename as string) || 'document',
                    format: (tc.arguments.format as string) || '',
                  })
                }
              }
            }
            setMessages(prev => [...prev, {
              id: Date.now(), role: 'tool' as const, content: event.result || '',
              tool_calls_json: null, tool_call_id: event.id || null,
              tool_name: event.name || null, created_at: new Date().toISOString()
            }])
          } else if (event.type === 'error') {
            setMessages(prev => [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: `Error: ${event.error}`, tool_calls_json: null,
              tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
            }])
          }
        }
        setMessages(prev => {
          if (assistantContent && prev[prev.length - 1]?.content !== assistantContent) {
            return [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: assistantContent, tool_calls_json: toolCalls.length ? toolCalls : null,
              tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
            }]
          }
          return prev
        })
      } catch (e: any) {
        if (e.name !== 'AbortError') {
          setMessages(prev => [...prev, {
            id: Date.now(), role: 'assistant' as const,
            content: `Error: ${e.message}`, tool_calls_json: null,
            tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
          }])
        }
      } finally {
        setExecutingTools(new Set())
        setStreaming(false)
        setAbortController(null)
        loadConversations()
      }
    } catch { /* ignore */ }
  }

  // Auth screens
  if (authLoading) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-950">
        <Loader2 className="w-8 h-8 animate-spin text-emerald-400" />
      </div>
    )
  }

  if (authStatus?.needs_setup) {
    return <SetupWizard onDone={(resp) => handleAuthDone(resp.user)} />
  }

  if (!currentUser) {
    return <LoginPage authStatus={authStatus || { needs_setup: false, registration_enabled: false }} onDone={(resp) => handleAuthDone(resp.user)} />
  }

  const isAdmin = currentUser.role === 'owner' || currentUser.role === 'admin'

  return (
    <div className="h-screen flex bg-gray-950 text-gray-100 overflow-hidden">
      {/* Sidebar */}
      <div className={`${showSidebar ? 'w-72' : 'w-0'} transition-all duration-200 border-r border-gray-800 flex flex-col overflow-hidden bg-gray-900`}>
        <div className="p-3 border-b border-gray-800 flex items-center justify-between">
          <h1 className="font-bold text-lg flex items-center gap-2">
            <Bot className="w-5 h-5 text-emerald-400" />
            LLMDash
          </h1>
          <button onClick={() => setShowSidebar(false)} className="p-1 hover:bg-gray-800 rounded">
            <ChevronLeft className="w-4 h-4" />
          </button>
        </div>
        <div className="p-2">
          <button onClick={createConv} className="w-full flex items-center gap-2 px-3 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors">
            <Plus className="w-4 h-4" /> New Chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {conversations.map(conv => (
            <div
              key={conv.id}
              onClick={() => selectConv(conv)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm group transition-colors ${
                activeConv?.id === conv.id ? 'bg-gray-700' : 'hover:bg-gray-800'
              }`}
            >
              <MessageSquare className="w-4 h-4 shrink-0 text-gray-400" />
              <span className="truncate flex-1">{conv.title}</span>
              <button
                onClick={e => { e.stopPropagation(); deleteConv(conv.id) }}
                className="p-1 hover:bg-red-600/20 rounded opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Trash2 className="w-3 h-3 text-red-400" />
              </button>
            </div>
          ))}
        </div>
        {models.find(m => m.id === selectedModelId) && (
          <div className="px-3 py-2 border-t border-gray-800 text-xs text-gray-500 flex items-center gap-2">
            <Bot className="w-3 h-3 text-emerald-400" />
            <span className="truncate">{models.find(m => m.id === selectedModelId)?.name}</span>
          </div>
        )}
        <div className="p-2 border-t border-gray-800 space-y-1">
          {isAdmin && (
            <button onClick={() => setShowAdmin(true)} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-800 rounded-lg text-sm">
              <Shield className="w-4 h-4 text-emerald-400" /> Admin Panel
            </button>
          )}
          <button onClick={() => setShowSubscription(true)} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-800 rounded-lg text-sm">
            <CreditCard className="w-4 h-4 text-emerald-400" /> Subscription
          </button>
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-gray-500">
            <span className="truncate flex-1">
              {currentUser.username}
              <span className={`ml-1 ${currentUser.role === 'owner' ? 'text-amber-400' : currentUser.role === 'admin' ? 'text-emerald-400' : 'text-gray-500'}`}>
                ({currentUser.role})
              </span>
            </span>
            <button onClick={handleLogout} className="p-1 hover:bg-gray-800 rounded text-gray-500 hover:text-red-400" title="Logout">
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="px-3 py-1 text-xs text-gray-500">
            <span className="font-mono">{(currentUser.token_usage || 0).toLocaleString()}</span> tokens used
            {currentUser.token_limit && (
              <span> / <span className={currentUser.token_usage >= currentUser.token_limit ? 'text-red-400' : ''}>{currentUser.token_limit.toLocaleString()}</span></span>
            )}
          </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {!showSidebar && (
          <div className="p-2 border-b border-gray-800 flex items-center justify-between">
            <button onClick={() => setShowSidebar(true)} className="p-1 hover:bg-gray-800 rounded">
              <ChevronRight className="w-4 h-4" />
            </button>
            <div className="text-xs text-gray-500 flex items-center gap-2">
              <span>{currentUser.username}</span>
              <button onClick={handleLogout} className="hover:text-red-400"><LogOut className="w-3 h-3" /></button>
            </div>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-gray-500 p-8">
              <Bot className="w-16 h-16 mb-4 text-gray-700" />
              <h2 className="text-xl font-semibold text-gray-400 mb-2">LLMDash</h2>
              <p className="text-sm text-center max-w-md">
                Multi-model AI chat with web search, document editing, HTML preview, and Alpine sandbox.
              </p>
              {models.length > 0 ? (
                <div className="relative mt-4">
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowModelPickerEmpty(!showModelPickerEmpty) }}
                    className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded-lg text-xs transition-colors"
                  >
                    <Bot className="w-3.5 h-3.5 text-emerald-400" />
                    <span>{models.find(m => m.id === selectedModelId)?.name || 'Select model'}</span>
                    <ChevronDown className="w-3 h-3" />
                  </button>
                  {showModelPickerEmpty && (
                    <div onClick={e => e.stopPropagation()} className="absolute left-1/2 -translate-x-1/2 mt-1 w-64 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden">
                      {models.map(m => (
                        <button
                          key={m.id}
                          onClick={() => { setSelectedModelId(m.id); closeModelPickers() }}
                          className={`w-full text-left px-3 py-2 text-xs hover:bg-gray-700 transition-colors flex items-center gap-2 ${m.id === selectedModelId ? 'bg-gray-700 text-emerald-400' : 'text-gray-300'}`}
                        >
                          <Bot className="w-3 h-3 shrink-0" />
                          <span className="truncate">{m.name}</span>
                          {m.id === selectedModelId && <Check className="w-3 h-3 shrink-0 ml-auto" />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs text-gray-600 mt-4">No models configured. {isAdmin ? 'Go to Admin Panel to set one up.' : 'Contact an admin.'}</p>
              )}
            </div>
          ) : (
            <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
              {messages.map((msg, i) => {
                const key = activeConv ? `${activeConv.id}:${i}` : ''
                const siblings = branchSiblings[key] || []
                const branchIdx = siblings.indexOf(activeConv?.id ?? 0)
                return (
                  <div key={msg.id || i}>
                    <MessageBubble
                      message={msg}
                      msgIndex={i}
                      messages={messages}
                      convId={activeConv?.id ?? 0}
                      onRegenerate={handleRegenerate}
                      onCopy={handleCopy}
                      copiedId={copiedId}
                      executingTools={executingTools}
                      setSidePanel={setSidePanel}
                    />
                    {siblings.length > 1 && (
                      <div className="flex items-center justify-center gap-1 mt-1 text-xs text-gray-500">
                        <button onClick={() => { const prev = (branchIdx - 1 + siblings.length) % siblings.length; handleSwitchBranch(siblings[prev]) }} className="p-0.5 hover:text-gray-300">
                          <ChevronUp className="w-4 h-4" />
                        </button>
                        <span>{branchIdx + 1}/{siblings.length}</span>
                        <button onClick={() => { const next = (branchIdx + 1) % siblings.length; handleSwitchBranch(siblings[next]) }} className="p-0.5 hover:text-gray-300">
                          <ChevronDown className="w-4 h-4" />
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
              <div ref={messagesEndRef} />
            </div>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-gray-800 p-4">
          <div className="max-w-4xl mx-auto">
            <div className="flex gap-2 items-end">
              <div className="flex-1 relative">
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={streaming ? 'Waiting for response...' : 'Type a message...'}
                  rows={1}
                  disabled={streaming}
                  className="w-full bg-gray-800 rounded-xl px-4 py-3 pr-12 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-emerald-500/50 disabled:opacity-50"
                  onInput={e => {
                    const el = e.currentTarget
                    el.style.height = 'auto'
                    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
                  }}
                />
              </div>
              <button
                onClick={streaming ? handleCancel : handleSend}
                disabled={!streaming && !input.trim()}
                className={`p-3 rounded-xl transition-colors ${
                  streaming ? 'bg-red-600 hover:bg-red-500' : 'bg-emerald-600 hover:bg-emerald-500 disabled:bg-gray-700 disabled:text-gray-500'
                }`}
              >
                {streaming ? <Square className="w-5 h-5" /> : <Send className="w-5 h-5" />}
              </button>
            </div>
            <div className="flex items-center gap-3 mt-2 text-xs relative">
              {models.length > 0 && (
                <button
                  onClick={(e) => { e.stopPropagation(); setShowModelPickerFooter(!showModelPickerFooter) }}
                  className="flex items-center gap-1.5 px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors text-gray-400 hover:text-gray-300"
                >
                  <Bot className="w-3 h-3 text-emerald-400" />
                  <span>{models.find(m => m.id === selectedModelId)?.name || 'Select'}</span>
                  <ChevronDown className="w-3 h-3" />
                </button>
              )}
              {showModelPickerFooter && models.length > 0 && (
                <div onClick={e => e.stopPropagation()} className="absolute bottom-full left-0 mb-1 w-64 bg-gray-800 border border-gray-700 rounded-lg shadow-xl z-50 overflow-hidden">
                  {models.map(m => (
                    <button
                      key={m.id}
                      onClick={() => { setSelectedModelId(m.id); closeModelPickers() }}
                      className={`w-full text-left px-3 py-2 text-xs hover:bg-gray-700 transition-colors flex items-center gap-2 ${m.id === selectedModelId ? 'bg-gray-700 text-emerald-400' : 'text-gray-300'}`}
                    >
                      <Bot className="w-3 h-3 shrink-0" />
                      <span className="truncate">{m.name}</span>
                      {m.id === selectedModelId && <Check className="w-3 h-3 shrink-0 ml-auto" />}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Document Preview Side Panel */}
      {sidePanel && (
        <div className="w-[420px] border-l border-gray-700 bg-gray-900 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700 shrink-0">
            <div className="flex items-center gap-2 text-xs min-w-0">
              <FileText className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
              <span className="font-mono text-emerald-300 truncate">{sidePanel.filename}.{sidePanel.format}</span>
              <span className="text-gray-500 shrink-0">({sidePanel.format.toUpperCase()})</span>
            </div>
            <button
              onClick={() => setSidePanel(null)}
              className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-gray-300"
              title="Hide preview"
            >
              <Minus className="w-4 h-4" />
            </button>
          </div>
          <div className="flex-1 bg-white">
            <iframe
              srcDoc={sidePanel.html}
              sandbox="allow-scripts"
              className="w-full h-full border-0"
              title="Document Preview"
            />
          </div>
        </div>
      )}

      {/* Admin Panel Modal */}
      {showAdmin && (
        <AdminPanel
          currentUser={currentUser}
          onClose={() => { setShowAdmin(false); loadModels() }}
          onRefreshModels={loadModels}
        />
      )}

      {/* Subscription Page Modal */}
      {showSubscription && (
        <SubscriptionPage
          currentUser={currentUser}
          onClose={() => setShowSubscription(false)}
        />
      )}
    </div>
  )
}

// --- Message Bubble ---

function MessageBubble({ message, msgIndex, messages, convId, onRegenerate, onCopy, copiedId, executingTools, setSidePanel }: {
  message: Message
  msgIndex: number
  messages: Message[]
  convId: number
  onRegenerate: (idx: number) => void
  onCopy: (content: string, id: number) => void
  copiedId: number | null
  executingTools: Set<string>
  setSidePanel: (panel: SidePanel | null) => void
}) {
  const isUser = message.role === 'user'
  const isTool = message.role === 'tool'
  const isAssistant = message.role === 'assistant'
  const toolCalls = message.tool_calls_json

  if (isTool && message.content) {
    const isHtmlRender = message.content.startsWith('HTML_RENDER:')
    const downloadMatch = message.content.match(/Download:\s*(\/api\/files\/\S+)/)
    if (isHtmlRender) {
      const html = atob(message.content.slice(12))
      return (
        <div>
          <div className="flex justify-start">
            <div className="max-w-full rounded-xl overflow-hidden border border-gray-700 bg-gray-900">
              <div className="flex items-center gap-2 px-3 py-2 bg-gray-800 text-xs text-gray-400 border-b border-gray-700">
                <Eye className="w-3.5 h-3.5" /> HTML Preview
              </div>
              <iframe srcDoc={html} sandbox="allow-scripts" className="w-full h-96 bg-white" title="HTML Preview" />
            </div>
          </div>
        </div>
      )
    }
    if (message.tool_name === 'edit_document') {
      const htmlRenderMatch = message.content.match(/HTML_RENDER:([A-Za-z0-9+/=]+)/)
      const visualFormat = htmlRenderMatch !== null

      let docFormat = ''
      let docFilename = ''
      let docContent = ''
      if (msgIndex > 0) {
        const prevMsg = messages[msgIndex - 1]
        if (prevMsg?.role === 'assistant' && prevMsg.tool_calls_json) {
          const matchingCall = prevMsg.tool_calls_json.find(tc => tc.id === message.tool_call_id)
          if (matchingCall) {
            docFormat = (matchingCall.arguments.format as string) || ''
            docFilename = (matchingCall.arguments.filename as string) || ''
            docContent = (matchingCall.arguments.content as string) || ''
          }
        }
      }
      const isCode = ['py','js','ts','html','css','json','xml','yaml','toml','sh','rs','go','java','c','cpp','sql','r','rb','php','lua','swift','kt','tf','ini','cfg','env','Dockerfile','Makefile'].includes(docFormat)
      return (
        <div>
          <div className="flex justify-start">
            <div className="max-w-3xl rounded-xl border border-gray-700 bg-gray-850 overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-2 bg-gray-800 text-xs text-gray-400 border-b border-gray-700">
                <FileText className="w-3.5 h-3.5 text-emerald-400" />
                <span className="font-mono text-emerald-300">{docFilename}.{docFormat}</span>
                <span className="text-gray-500">({docFormat.toUpperCase()})</span>
              </div>
              {visualFormat ? (
                <div className="p-3 bg-gray-900 flex flex-col items-center gap-2">
                  <p className="text-xs text-gray-400">Document preview available</p>
                  <button
                    onClick={() => setSidePanel({
                      html: atob(htmlRenderMatch![1]),
                      filename: docFilename,
                      format: docFormat,
                    })}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-sm font-medium transition-colors cursor-pointer border-0"
                  >
                    <Eye className="w-4 h-4" />
                    Open Preview
                  </button>
                </div>
              ) : docContent ? (
                <div className="p-3 bg-gray-900">
                  {isCode ? (
                    <MarkdownRenderer content={'```' + docFormat + '\n' + docContent.slice(0, 8000) + (docContent.length > 8000 ? '\n\n... (truncated)' : '') + '\n```'} />
                  ) : (
                    <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono max-h-96 overflow-y-auto">
                      {docContent.length > 8000 ? docContent.slice(0, 8000) + '\n\n... (truncated)' : docContent}
                    </pre>
                  )}
                </div>
              ) : null}
              <div className="px-3 py-2 bg-gray-800/50 text-xs text-gray-500 border-t border-gray-700/50 truncate">
                {message.content.replace(/\n?HTML_RENDER:[A-Za-z0-9+/=]+/, '').trim()}
              </div>
              {downloadMatch && (
                <div className="px-3 pb-2 bg-gray-800/50">
                  <button
                    onClick={async () => {
                      try {
                        const token = localStorage.getItem('llmdash_token');
                        const headers: Record<string, string> = {};
                        if (token) headers['Authorization'] = `Bearer ${token}`;
                        const res = await fetch(downloadMatch![1], { headers });
                        if (!res.ok) throw new Error(`Download failed: ${res.status}`);
                        const blob = await res.blob();
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = downloadMatch![1].split('/').pop() || 'download';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                      } catch (e) {
                        console.error('Download error:', e);
                      }
                    }}
                    className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium transition-colors cursor-pointer border-0"
                  >
                    <Download className="w-3.5 h-3.5" />
                    Download {downloadMatch![1].split('/').pop()}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )
    }
    return (
      <div>
        <div className="flex justify-start">
          <div className="max-w-2xl rounded-xl bg-gray-800/50 border border-gray-700/50 px-4 py-2">
            <div className="flex items-center gap-2 text-xs text-gray-500 mb-1">
              {message.tool_name === 'web_search' && <Globe className="w-3 h-3" />}
              {message.tool_name === 'web_scrape' && <Search className="w-3 h-3" />}
              {message.tool_name === 'run_command' && <Terminal className="w-3 h-3" />}
              {message.tool_name === 'render_html' && <Eye className="w-3 h-3" />}
              <span className="font-mono">{message.tool_name}</span>
            </div>
            <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono">
              {message.content.length > 500 ? message.content.slice(0, 500) + '...' : message.content}
            </pre>
            {downloadMatch && (
              <button
                onClick={async () => {
                  try {
                    const token = localStorage.getItem('llmdash_token');
                    const headers: Record<string, string> = {};
                    if (token) headers['Authorization'] = `Bearer ${token}`;
                    const res = await fetch(downloadMatch[1], { headers });
                    if (!res.ok) throw new Error(`Download failed: ${res.status}`);
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = downloadMatch[1].split('/').pop() || 'download';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                  } catch (e) {
                    console.error('Download error:', e);
                  }
                }}
                className="mt-2 inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-medium transition-colors cursor-pointer border-0"
              >
                <Download className="w-3.5 h-3.5" />
                Download {downloadMatch[1].split('/').pop()}
              </button>
            )}
          </div>
        </div>
        <div className="flex gap-1 mt-0.5 justify-start ml-10">
          <button onClick={() => onCopy(message.content || '', message.id)} className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-gray-300" title="Copy">
            {copiedId === message.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>
    )
  }

  if (toolCalls && Array.isArray(toolCalls) && toolCalls.length > 0) {
    return (
      <div>
        <div className="flex justify-start">
          <div className="max-w-2xl space-y-2">
            {message.content && <MarkdownRenderer content={message.content} />}
            <div className="flex flex-wrap gap-2">
              {toolCalls.map((tc, i) => {
                const isExecuting = executingTools.has(tc.id)
                return (
                <div key={i} className={`flex items-center gap-2 px-3 py-2 bg-gray-800 rounded-lg border text-xs ${isExecuting ? 'border-emerald-500/40 animate-pulse' : 'border-gray-700'}`}>
                  {isExecuting ? (
                    <Loader2 className="w-3.5 h-3.5 text-emerald-400 animate-spin" />
                  ) : (
                    <Bot className="w-3.5 h-3.5 text-emerald-400" />
                  )}
                  <span className="font-mono text-emerald-300">{tc.name}</span>
                  <span className={isExecuting ? 'text-emerald-400' : 'text-gray-500'}>{isExecuting ? 'executing...' : 'calling...'}</span>
                </div>
                )
              })}
            </div>
          </div>
        </div>
        {message.content && (
          <div className="flex gap-1 mt-0.5 justify-start ml-10">
            <button onClick={() => onCopy(message.content || '', message.id)} className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-gray-300" title="Copy">
              {copiedId === message.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
          </div>
        )}
      </div>
    )
  }

  const content = message.content || ''
  const hasHtmlRender = content.includes('HTML_RENDER:')

  if (hasHtmlRender) {
    const parts = content.split(/(HTML_RENDER:[A-Za-z0-9+/=]+)/)
    return (
      <div>
        <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
          <div className={`max-w-[80%] ${isUser ? 'bg-emerald-700' : 'bg-gray-800'} rounded-xl px-4 py-2`}>
            {parts.map((part, i) => {
              if (part.startsWith('HTML_RENDER:')) {
                try {
                  const html = atob(part.slice(12))
                  return (
                    <div key={i} className="my-2 rounded-lg overflow-hidden border border-gray-700">
                      <div className="flex items-center gap-2 px-3 py-1.5 bg-gray-900 text-xs text-gray-400 border-b border-gray-700">
                        <Eye className="w-3 h-3" /> Preview
                      </div>
                      <iframe srcDoc={html} sandbox="allow-scripts" className="w-full h-96 bg-white" title="Preview" />
                    </div>
                  )
                } catch { return <span key={i}>{part}</span> }
              }
              return <MarkdownRenderer key={i} content={part} />
            })}
          </div>
        </div>
        <div className={`flex gap-1 mt-0.5 ${isUser ? 'justify-end mr-10' : 'justify-start ml-10'}`}>
          <button onClick={() => onCopy(content, message.id)} className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-gray-300" title="Copy">
            {copiedId === message.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
          {isUser && (
            <button onClick={() => onRegenerate(msgIndex)} className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-emerald-400" title="Regenerate">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div>
      <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        {isAssistant && (
          <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center mr-2 mt-0.5 shrink-0">
            <Bot className="w-4 h-4" />
          </div>
        )}
        <div className={`max-w-[75%] ${isUser ? 'bg-emerald-700' : 'bg-gray-800'} rounded-xl px-4 py-2.5`}>
          <MarkdownRenderer content={content} />
        </div>
        {isUser && (
          <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center ml-2 mt-0.5 shrink-0">
            <span className="text-xs font-bold">U</span>
          </div>
        )}
      </div>
      <div className={`flex gap-1 mt-0.5 ${isUser ? 'justify-end mr-10' : 'justify-start ml-10'}`}>
        <button onClick={() => onCopy(content, message.id)} className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-gray-300" title="Copy">
          {copiedId === message.id ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
        {isUser && (
          <button onClick={() => onRegenerate(msgIndex)} className="p-1 hover:bg-gray-700 rounded transition-colors text-gray-500 hover:text-emerald-400" title="Regenerate">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

export default App
