import React, { useState, useEffect, useRef, useCallback } from 'react'
import { api } from './api'
import type { ModelConfig, Conversation, Message, StreamEvent, ToolCall, User, AuthStatus, GenerateStatus, AttachmentRecord } from './types'

const LAST_ACTIVE_CONV_KEY = 'llmdash_active_conv'

interface SidePanel {
  html: string
  filename: string
  format: string
}
import {
  Send, Plus, Key, MessageSquare, Trash2, ChevronLeft,
  ChevronRight, Wrench, Bot, Loader2, Terminal, Globe, FileText, Eye, Search,
  Copy, Check, RefreshCw, Square, ChevronUp, ChevronDown, Download,
  Shield, LogOut, Settings, Minus, CreditCard, Brain, Image, Paperclip, X, File, Palette
} from 'lucide-react'
import MarkdownRenderer from './components/MarkdownRenderer'
import SetupWizard from './components/SetupWizard'
import LoginPage from './components/LoginPage'
import AdminPanel from './components/AdminPanel'
import SubscriptionPage from './components/SubscriptionPage'
import CustomCssPanel from './components/CustomCssPanel'
import VoiceButton from './components/VoiceButton'
import { DEFAULT_CSS } from './css-preset'

const STYLE_ID = 'llmdash-user-css'

function injectUserCss(css: string) {
  const el = document.getElementById(STYLE_ID) as HTMLStyleElement | null
  if (el) {
    el.textContent = css || DEFAULT_CSS
  }
}

function mergeMessages(local: Message[], server: Message[]): Message[] {
  const serverById = new Map<number, Message>()
  for (const m of server) serverById.set(m.id, m)
  const localById = new Map<number, Message>()
  for (const m of local) localById.set(m.id, m)
  const result: Message[] = []
  const seen = new Set<number>()
  for (const sm of server) {
    const lm = localById.get(sm.id)
    if (lm && lm.status === 'generating') {
      result.push(lm)
    } else if (sm.role === 'tool' && !sm.tool_name && lm?.tool_name) {
      result.push(lm)
    } else {
      result.push(sm)
    }
    seen.add(sm.id)
  }
  for (const lm of local) {
    if (!seen.has(lm.id) && lm.status === 'generating') {
      result.push(lm)
    }
  }
  return result
}

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
  const [imageGenSize, setImageGenSize] = useState('1024x1024')
  const [showSidebar, setShowSidebar] = useState(true)
  const [showAdmin, setShowAdmin] = useState(false)
  const [showSubscription, setShowSubscription] = useState(false)
  const [showCustomCss, setShowCustomCss] = useState(false)
  const [cssUndoToast, setCssUndoToast] = useState<{ previousCss: string } | null>(null)
  const cssPreviousRef = useRef<string>(DEFAULT_CSS)
  const [showModelPickerFooter, setShowModelPickerFooter] = useState(false)
  const [showModelPickerEmpty, setShowModelPickerEmpty] = useState(false)
  const closeModelPickers = () => { setShowModelPickerFooter(false); setShowModelPickerEmpty(false) }
  const [abortController, setAbortController] = useState<AbortController | null>(null)
  const [branchSiblings, setBranchSiblings] = useState<Record<string, number[]>>({})
  const [branchConvToKey, setBranchConvToKey] = useState<Record<number, string>>({})
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [executingTools, setExecutingTools] = useState<Set<string>>(new Set())
  const [expandedToolCalls, setExpandedToolCalls] = useState<Set<string>>(new Set())
  const [sidePanel, setSidePanel] = useState<SidePanel | null>(null)

  const toggleToolCall = useCallback((id: string) => {
    setExpandedToolCalls(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const [attachments, setAttachments] = useState<AttachmentRecord[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const messagesEndRef = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    try {
      const convs = await api.conversations.list()
      setConversations(convs)
      return convs as Conversation[]
    } catch { return [] as Conversation[] }
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
      const init = async () => {
        const convs = await loadConversations()
        const savedConvId = localStorage.getItem(LAST_ACTIVE_CONV_KEY)
        if (savedConvId) {
          const conv = convs.find(c => c.id === parseInt(savedConvId))
          if (conv) {
            selectConv(conv)
          } else {
            localStorage.removeItem(LAST_ACTIVE_CONV_KEY)
          }
        }
      }
      init()
      loadModels()
      api.auth.css.get().then(res => {
        if (res.css) {
          injectUserCss(res.css)
          cssPreviousRef.current = res.css
        }
      }).catch(() => {})
    }
  }, [currentUser, loadConversations, loadModels])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    if (activeConv) {
      localStorage.setItem(LAST_ACTIVE_CONV_KEY, String(activeConv.id))
    }
  }, [activeConv])

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
    localStorage.removeItem(LAST_ACTIVE_CONV_KEY)
    try {
      const status = await api.auth.status()
      setAuthStatus(status)
    } catch {
      setAuthStatus({ needs_setup: false, registration_enabled: false })
    }
  }

  const createConv = async () => {
    setSidePanel(null)
    setAttachments([])
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
      if (activeConv?.id === id) { setActiveConv(null); setMessages([]); localStorage.removeItem(LAST_ACTIVE_CONV_KEY) }
    } catch { /* ignore */ }
  }

  const selectConv = async (conv: Conversation) => {
    setActiveConv(conv)
    setSidePanel(null)
    setAttachments([])
    localStorage.setItem(LAST_ACTIVE_CONV_KEY, String(conv.id))
    try {
      const msgs = await api.conversations.messages(conv.id)
      setMessages(msgs)
      const lastAssistant = msgs.filter((m: Message) => m.role === 'assistant').pop()
      if (lastAssistant && lastAssistant.status === 'generating') {
        setStreaming(true)
        resumeGeneration(conv.id, lastAssistant)
      } else {
        setStreaming(false)
      }
    } catch { setMessages([]) }
  }

  const resumeGeneration = async (convId: number, draftMsg?: Message) => {
    const controller = new AbortController()
    setAbortController(controller)

    let generationResumed = false
    try {
      for await (const event of api.chat.resume(convId, controller.signal)) {
        generationResumed = true
        if (event.type === 'content') {
          const content = event.content || ''
          const reasoning = event.reasoning_content || null
          setMessages(prev => {
            const idx = prev.findIndex(m => m.role === 'assistant' && m.status === 'generating')
            if (idx >= 0) {
              const updated = [...prev]
              updated[idx] = { ...updated[idx], content, reasoning_content: reasoning || updated[idx].reasoning_content, tool_calls_json: event.tool_calls || null }
              return updated
            }
            return prev
          })
        } else if (event.type === 'content_delta') {
          setMessages(prev => {
            const idx = prev.findIndex(m => m.role === 'assistant' && m.status === 'generating')
            if (idx >= 0) {
              const updated = [...prev]
              updated[idx] = { ...updated[idx], content: (updated[idx].content || '') + (event.content || '') }
              return updated
            }
            return [...prev, {
              id: convId * -1, role: 'assistant' as const,
              content: event.content || '', tool_calls_json: null,
              tool_call_id: null, tool_name: null, status: 'generating',
              created_at: new Date().toISOString()
            }]
          })
        } else if (event.type === 'reasoning_delta') {
          setMessages(prev => {
            const idx = prev.findIndex(m => m.role === 'assistant' && m.status === 'generating')
            if (idx >= 0) {
              const updated = [...prev]
              updated[idx] = { ...updated[idx], reasoning_content: (updated[idx].reasoning_content || '') + (event.content || '') }
              return updated
            }
            return [...prev, {
              id: convId * -1, role: 'assistant' as const,
              content: '', reasoning_content: event.content || '', tool_calls_json: null,
              tool_call_id: null, tool_name: null, status: 'generating',
              created_at: new Date().toISOString()
            }]
          })
        } else if (event.type === 'tool_calls') {
          setMessages(prev => {
            const idx = prev.findIndex(m => m.role === 'assistant' && m.status === 'generating')
            const tc = (event as any).tool_calls || null
            if (idx >= 0) {
              const updated = [...prev]
              updated[idx] = { ...updated[idx], content: event.content || updated[idx].content || '', tool_calls_json: tc }
              return updated
            }
            return [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: event.content || '', tool_calls_json: tc,
              tool_call_id: null, tool_name: null, status: 'generating',
              created_at: new Date().toISOString()
            }]
          })
          const cssTool = (event as any).tool_calls?.find((tc: any) => tc.name === 'set_user_css')
          if (cssTool?.arguments?.css) {
            const cur = (document.getElementById(STYLE_ID) as HTMLStyleElement)?.textContent || DEFAULT_CSS
            cssPreviousRef.current = cur
            injectUserCss(cssTool.arguments.css as string)
            setCssUndoToast({ previousCss: cur })
          }
        } else if (event.type === 'tool_start') {
          if (event.id) setExecutingTools(prev => new Set(prev).add(event.id!))
        } else if (event.type === 'tool_result') {
          if (event.id) setExecutingTools(prev => { const next = new Set(prev); next.delete(event.id!); return next })
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
    } catch (e: any) {
      if (e.name === 'AbortError') return
      if (!generationResumed) {
        pollForCompletion(convId, Date.now(), draftMsg?.id)
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

  const handleSend = async () => {
    if ((!input.trim() && attachments.length === 0) || streaming) return
    setSidePanel(null)
    const modelId = selectedModelId
    if (!modelId) { alert('No enabled model configured. Ask an admin to set one up.'); return }

    const selectedModel = models.find(m => m.id === modelId)

    if (selectedModel?.model_type === 'image') {
      let conv = activeConv
      if (!conv) {
        try {
          conv = await api.conversations.create({ title: 'New Chat', model_id: modelId })
          setConversations(prev => [conv!, ...prev])
          setActiveConv(conv!)
        } catch { return }
      }

      setMessages(prev => [...prev, {
        id: Date.now(), role: 'user', content: input,
        tool_calls_json: null, tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
      }])
      const prompt = input
      setInput('')
      setAttachments([])
      setStreaming(true)

      try {
        const result = await api.chat.generateImage(conv.id, prompt, modelId, imageGenSize, 1)
        const images: string[] = result.images || []
        const imgMsg: Message = {
          id: Date.now() + 1,
          role: 'assistant',
          content: JSON.stringify({ images, revised_prompt: result.revised_prompt, prompt, size: imageGenSize }),
          tool_calls_json: null, tool_call_id: null, tool_name: null,
          created_at: new Date().toISOString()
        }
        setMessages(prev => [...prev, imgMsg])
      } catch (e: any) {
        setMessages(prev => [...prev, {
          id: Date.now() + 1, role: 'assistant' as const,
          content: `Error: ${e.message}`, tool_calls_json: null,
          tool_call_id: null, tool_name: null, created_at: new Date().toISOString()
        }])
      } finally {
        setStreaming(false)
        setAbortController(null)
        loadConversations()
        if (currentUser) {
          try { setCurrentUser(await api.auth.me()) } catch {}
        }
      }
      return
    }

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
    setAttachments([])
    setStreaming(true)

    const controller = new AbortController()
    setAbortController(controller)

    try {
      let assistantContent = ''
      let assistantReasoning = ''
      const toolCalls: ToolCall[] = []

      for await (const event of api.chat.send(conv.id, userMsg.content || '', modelId, controller.signal, attachments.length > 0 ? attachments : undefined)) {
          if (event.type === 'content_delta') {
            assistantContent += (event.content || '')
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === (conv?.id || 0) * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: assistantContent, reasoning_content: assistantReasoning || updated[idx].reasoning_content }
                return updated
              }
              return [...prev, {
                id: (conv?.id || 0) * -1, role: 'assistant' as const,
                content: assistantContent, reasoning_content: assistantReasoning, tool_calls_json: null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'reasoning_delta') {
            assistantReasoning += (event.content || '')
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === (conv?.id || 0) * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], reasoning_content: assistantReasoning }
                return updated
              }
              return [...prev, {
                id: (conv?.id || 0) * -1, role: 'assistant' as const,
                content: '', reasoning_content: assistantReasoning, tool_calls_json: null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'content') {
            assistantContent = event.content || ''
            assistantReasoning = event.reasoning_content || assistantReasoning
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === (conv?.id || 0) * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: assistantContent, reasoning_content: assistantReasoning || updated[idx].reasoning_content }
                return updated
              }
              return [...prev, {
                id: (conv?.id || 0) * -1, role: 'assistant' as const,
                content: assistantContent, reasoning_content: assistantReasoning, tool_calls_json: null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'tool_calls') {
            toolCalls.length = 0
            toolCalls.push(...(event.tool_calls || []))
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === (conv?.id || 0) * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: assistantContent, tool_calls_json: event.tool_calls || null }
                return updated
              }
              return [...prev, {
                id: (conv?.id || 0) * -1, role: 'assistant' as const,
                content: event.content || '', tool_calls_json: event.tool_calls || null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
            const cssTool2 = event.tool_calls?.find((tc: any) => tc.name === 'set_user_css')
            if (cssTool2?.arguments?.css) {
              const cur = (document.getElementById(STYLE_ID) as HTMLStyleElement)?.textContent || DEFAULT_CSS
              cssPreviousRef.current = cur
              injectUserCss(cssTool2.arguments.css as string)
              setCssUndoToast({ previousCss: cur })
            }
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
          if (assistantContent && prev.findIndex(m => m.id === (conv?.id || 0) * -1) < 0) {
            return [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: assistantContent, tool_calls_json: null,
              tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
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

  const pollForCompletion = (convId: number, sentAt: number, draftMsgId?: number) => {
    let lastMsgId = 0
    const interval = setInterval(async () => {
      if (Date.now() - sentAt > 600000) { clearInterval(interval); return }
      try {
        const status = await api.chat.generationStatus(convId)
        if (!status.generating) {
          clearInterval(interval)
          if (convId === activeConv?.id) {
            setStreaming(false)
            setExecutingTools(new Set())
          }
          const full = await api.conversations.messages(convId)
          setMessages(prev => mergeMessages(prev, full))
          loadConversations()
          return
        }
        const msgs = await api.conversations.messages(convId)
        const lastAssistant = msgs.filter((m: Message) => m.role === 'assistant').pop()
        if (lastAssistant && lastAssistant.id !== lastMsgId) {
          lastMsgId = lastAssistant.id
          setMessages(prev => mergeMessages(prev, msgs))
        }
      } catch { /* keep polling */ }
    }, 500)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  const ALLOWED_FILE_EXTS = ['.png','.jpg','.jpeg','.gif','.webp','.bmp','.tiff','.tif','.txt','.csv','.json','.xml','.yaml','.yml','.toml','.ini','.cfg','.log','.md','.py','.js','.ts','.jsx','.tsx','.html','.css','.scss','.less','.sh','.bash','.zsh','.rs','.go','.java','.c','.cpp','.h','.hpp','.sql','.r','.rb','.php','.lua','.swift','.kt','.tf','.env','.gitignore','.dockerfile','.makefile','.conf','.cnf','.gradle','.properties','.lock','.pdf']

  const handleFileUpload = async (files: FileList | null) => {
    if (!files || files.length === 0 || uploading) return
    setUploading(true)
    const newAttachments: AttachmentRecord[] = []
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const ext = '.' + file.name.split('.').pop()?.toLowerCase()
      if (!ALLOWED_FILE_EXTS.includes(ext)) {
        alert(`File type ${ext} is not supported`)
        continue
      }
      try {
        const result = await api.chat.upload(file)
        newAttachments.push({
          filename: file.name,
          file_type: result.file_type,
          file_path: result.file_path,
          ocr_text: result.ocr_text,
        })
      } catch (e: any) {
        alert(`Upload failed for ${file.name}: ${e.message}`)
      }
    }
    setAttachments(prev => [...prev, ...newAttachments])
    setUploading(false)
  }

  const removeAttachment = (index: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== index))
  }

  const handleCancel = async () => {
    if (activeConv) {
      try { await api.chat.cancel(activeConv.id) } catch {}
    }
    abortController?.abort()
    setSidePanel(null)
  }

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
        let assistantReasoning = ''
        const toolCalls: ToolCall[] = []
        for await (const event of api.chat.send(branch.id, content, modelId, controller.signal, msg.attachments_json && msg.attachments_json.length > 0 ? msg.attachments_json : undefined)) {
           if (event.type === 'content_delta') {
            assistantContent += (event.content || '')
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === branch.id * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: assistantContent, reasoning_content: assistantReasoning || updated[idx].reasoning_content }
                return updated
              }
              return [...prev, {
                id: branch.id * -1, role: 'assistant' as const,
                content: assistantContent, reasoning_content: assistantReasoning, tool_calls_json: null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'reasoning_delta') {
            assistantReasoning += (event.content || '')
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === branch.id * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], reasoning_content: assistantReasoning }
                return updated
              }
              return [...prev, {
                id: branch.id * -1, role: 'assistant' as const,
                content: '', reasoning_content: assistantReasoning, tool_calls_json: null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'content') {
            assistantContent = event.content || ''
            assistantReasoning = event.reasoning_content || assistantReasoning
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === branch.id * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: assistantContent, reasoning_content: assistantReasoning || updated[idx].reasoning_content }
                return updated
              }
              return [...prev, {
                id: branch.id * -1, role: 'assistant' as const,
                content: assistantContent, reasoning_content: assistantReasoning, tool_calls_json: null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
          } else if (event.type === 'tool_calls') {
            toolCalls.length = 0
            toolCalls.push(...(event.tool_calls || []))
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === branch.id * -1)
              if (idx >= 0) {
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: assistantContent, tool_calls_json: event.tool_calls || null }
                return updated
              }
              return [...prev, {
                id: branch.id * -1, role: 'assistant' as const,
                content: event.content || '', tool_calls_json: event.tool_calls || null,
                tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
              }]
            })
            const cssTool3 = event.tool_calls?.find((tc: any) => tc.name === 'set_user_css')
            if (cssTool3?.arguments?.css) {
              const cur = (document.getElementById(STYLE_ID) as HTMLStyleElement)?.textContent || DEFAULT_CSS
              cssPreviousRef.current = cur
              injectUserCss(cssTool3.arguments.css as string)
              setCssUndoToast({ previousCss: cur })
            }
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
          if (assistantContent && prev.findIndex(m => m.id === branch.id * -1) < 0) {
            return [...prev, {
              id: Date.now(), role: 'assistant' as const,
              content: assistantContent, tool_calls_json: null,
              tool_call_id: null, tool_name: null, status: 'generating', created_at: new Date().toISOString()
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
      <div className="h-screen flex items-center justify-center bg-theme-bg">
        <Loader2 className="w-8 h-8 animate-spin text-theme-accent-text" />
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
    <div className="h-screen flex bg-theme-bg text-theme-text overflow-hidden">
      {/* Sidebar */}
      <div className={`${showSidebar ? 'w-72' : 'w-0'} transition-all duration-200 border-r border-theme-border flex flex-col overflow-hidden bg-theme-bg-secondary`}>
        <div className="p-3 border-b border-theme-border flex items-center justify-between">
          <h1 className="font-bold text-lg flex items-center gap-2">
            <Bot className="w-5 h-5 text-theme-accent-text" />
            LLMDash
          </h1>
          <button onClick={() => setShowSidebar(false)} className="p-1 hover:bg-theme-bg-elevated rounded">
            <ChevronLeft className="w-4 h-4" />
          </button>
        </div>
        <div className="p-2">
          <button onClick={createConv} className="w-full flex items-center gap-2 px-3 py-2 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-sm font-medium transition-colors">
            <Plus className="w-4 h-4" /> New Chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {conversations.map(conv => (
            <div
              key={conv.id}
              onClick={() => selectConv(conv)}
              className={`flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer text-sm group transition-colors ${
                activeConv?.id === conv.id ? 'bg-theme-bg-hover' : 'hover:bg-theme-bg-elevated'
              }`}
            >
              <MessageSquare className="w-4 h-4 shrink-0 text-theme-subtle" />
              <span className="truncate flex-1">{conv.title}</span>
              <button
                onClick={e => { e.stopPropagation(); deleteConv(conv.id) }}
                className="p-1 hover:bg-theme-danger/20 rounded opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Trash2 className="w-3 h-3 text-theme-danger-text" />
              </button>
            </div>
          ))}
        </div>
        {models.find(m => m.id === selectedModelId) && (
          <div className="px-3 py-2 border-t border-theme-border text-xs text-theme-muted flex items-center gap-2">
            {models.find(m => m.id === selectedModelId)?.model_type === 'image'
              ? <Image className="w-3 h-3 text-theme-purple" />
              : <Bot className="w-3 h-3 text-theme-accent-text" />}
            <span className="truncate">{models.find(m => m.id === selectedModelId)?.name}</span>
          </div>
        )}
        <div className="p-2 border-t border-theme-border space-y-1">
          {isAdmin && (
            <button onClick={() => setShowAdmin(true)} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-theme-bg-elevated rounded-lg text-sm">
              <Shield className="w-4 h-4 text-theme-accent-text" /> Admin Panel
            </button>
          )}
          <button onClick={() => setShowSubscription(true)} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-theme-bg-elevated rounded-lg text-sm">
            <CreditCard className="w-4 h-4 text-theme-accent-text" /> Subscription
          </button>
          <button onClick={() => setShowCustomCss(true)} className="w-full flex items-center gap-2 px-3 py-2 hover:bg-theme-bg-elevated rounded-lg text-sm">
            <Palette className="w-4 h-4 text-theme-accent-text" /> Custom CSS
          </button>
          <div className="flex items-center gap-2 px-3 py-2 text-xs text-theme-muted">
            <span className="truncate flex-1">
              {currentUser.username}
              <span className={`ml-1 ${currentUser.role === 'owner' ? 'text-theme-amber' : currentUser.role === 'admin' ? 'text-theme-accent-text' : 'text-theme-muted'}`}>
                ({currentUser.role})
              </span>
            </span>
            <button onClick={handleLogout} className="p-1 hover:bg-theme-bg-elevated rounded text-theme-muted hover:text-theme-danger-text" title="Logout">
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="px-3 py-1 text-xs text-theme-muted">
            <span className="font-mono">{(() => {
              const modelUsage = selectedModelId && currentUser.token_usage_by_model ? currentUser.token_usage_by_model[selectedModelId]?.token_usage : undefined
              return modelUsage !== undefined ? modelUsage.toLocaleString() : (currentUser.token_usage || 0).toLocaleString()
            })()}</span> tokens used
            {selectedModelId && currentUser.token_usage_by_model && currentUser.token_usage_by_model[selectedModelId] !== undefined && (
              <span className="text-theme-subtle"> on this model</span>
            )}
            {!selectedModelId && currentUser.token_limit && (
              <span> / <span className={currentUser.token_usage >= currentUser.token_limit ? 'text-theme-danger-text' : ''}>{currentUser.token_limit.toLocaleString()}</span></span>
            )}
          </div>
          <div className="px-3 py-1 text-xs text-theme-muted">
            <span className="font-mono">{(currentUser.image_usage || 0).toLocaleString()}</span> images used
            {currentUser.image_limit && (
              <span> / <span className={currentUser.image_usage >= currentUser.image_limit ? 'text-theme-danger-text' : ''}>{currentUser.image_limit.toLocaleString()}</span></span>
            )}
          </div>
        </div>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {!showSidebar && (
          <div className="p-2 border-b border-theme-border flex items-center justify-between">
            <button onClick={() => setShowSidebar(true)} className="p-1 hover:bg-theme-bg-elevated rounded">
              <ChevronRight className="w-4 h-4" />
            </button>
            <div className="text-xs text-theme-muted flex items-center gap-2">
              <span>{currentUser.username}</span>
              <button onClick={handleLogout} className="hover:text-theme-danger-text"><LogOut className="w-3 h-3" /></button>
            </div>
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          {messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-theme-muted p-8">
              <Bot className="w-16 h-16 mb-4 text-theme-icon-muted" />
              <h2 className="text-xl font-semibold text-theme-subtle mb-2">LLMDash</h2>
              <p className="text-sm text-center max-w-md">
                Multi-model AI chat with web search, document editing, HTML preview, and Alpine sandbox.
              </p>
              {models.length > 0 ? (
                <div className="relative mt-4">
                  <button
                    onClick={(e) => { e.stopPropagation(); setShowModelPickerEmpty(!showModelPickerEmpty) }}
                    className="flex items-center gap-2 px-3 py-1.5 bg-theme-bg-elevated hover:bg-theme-bg-hover rounded-lg text-xs transition-colors"
                  >
                    {models.find(m => m.id === selectedModelId)?.model_type === 'image'
                      ? <Image className="w-3.5 h-3.5 text-theme-purple" />
                      : <Bot className="w-3.5 h-3.5 text-theme-accent-text" />}
                    <span>{models.find(m => m.id === selectedModelId)?.name || 'Select model'}</span>
                    <ChevronDown className="w-3 h-3" />
                  </button>
                  {showModelPickerEmpty && (
                    <div onClick={e => e.stopPropagation()} className="absolute left-1/2 -translate-x-1/2 mt-1 w-64 bg-theme-bg-elevated border border-theme-border-light rounded-lg shadow-xl z-50 overflow-hidden">
                      {models.map(m => (
                        <button
                          key={m.id}
                          onClick={() => { setSelectedModelId(m.id); closeModelPickers() }}
                          className={`w-full text-left px-3 py-2 text-xs hover:bg-theme-bg-hover transition-colors flex items-center gap-2 ${m.id === selectedModelId ? 'bg-theme-bg-hover text-theme-accent-text' : 'text-theme-text-secondary'}`}
                        >
                          {m.model_type === 'image'
                            ? <Image className="w-3 h-3 shrink-0 text-theme-purple" />
                            : <Bot className="w-3 h-3 shrink-0 text-theme-accent-text" />}
                          <span className="truncate">{m.name}</span>
                          {m.id === selectedModelId && <Check className="w-3 h-3 shrink-0 ml-auto" />}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-xs text-theme-subtle mt-4">No models configured. {isAdmin ? 'Go to Admin Panel to set one up.' : 'Contact an admin.'}</p>
              )}
            </div>
          ) : (
            <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
              {messages.map((msg, i) => {
                const key = activeConv ? `${activeConv.id}:${i}` : ''
                const siblings = branchSiblings[key] || []
                const branchIdx = siblings.indexOf(activeConv?.id ?? 0)
                return (
                  <div key={`${msg.role}-${msg.tool_call_id ?? ''}-${msg.id ?? i}`}>
                    <MessageBubble
                      message={msg}
                      msgIndex={i}
                      messages={messages}
                      convId={activeConv?.id ?? 0}
                      onRegenerate={handleRegenerate}
                      onCopy={handleCopy}
                      copiedId={copiedId}
                      executingTools={executingTools}
                      expandedToolCalls={expandedToolCalls}
                      onToggleToolCall={toggleToolCall}
                      setSidePanel={setSidePanel}
                    />
                    {siblings.length > 1 && (
                      <div className="flex items-center justify-center gap-1 mt-1 text-xs text-theme-muted">
                        <button onClick={() => { const prev = (branchIdx - 1 + siblings.length) % siblings.length; handleSwitchBranch(siblings[prev]) }} className="p-0.5 hover:text-theme-text">
                          <ChevronUp className="w-4 h-4" />
                        </button>
                        <span>{branchIdx + 1}/{siblings.length}</span>
                        <button onClick={() => { const next = (branchIdx + 1) % siblings.length; handleSwitchBranch(siblings[next]) }} className="p-0.5 hover:text-theme-text">
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
        <div className="border-t border-theme-border p-4">
          <div className="max-w-4xl mx-auto">
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {attachments.map((att, i) => (
                  <div key={i} className="flex items-center gap-1.5 bg-theme-bg-elevated rounded-lg px-3 py-1.5 text-xs border border-theme-border-light">
                    {['.png','.jpg','.jpeg','.gif','.webp','.bmp'].includes(att.file_type.toLowerCase()) ? (
                      <Image className="w-3.5 h-3.5 text-theme-purple" />
                    ) : (
                      <File className="w-3.5 h-3.5 text-theme-accent-text" />
                    )}
                    <span className="text-theme-text-secondary truncate max-w-[150px]">{att.filename}</span>
                    <button
                      onClick={() => removeAttachment(i)}
                      className="p-0.5 hover:bg-theme-danger/20 rounded text-theme-muted hover:text-theme-danger-text"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-2 items-end">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".png,.jpg,.jpeg,.gif,.webp,.bmp,.tiff,.tif,.txt,.csv,.json,.xml,.yaml,.yml,.toml,.ini,.cfg,.log,.md,.py,.js,.ts,.jsx,.tsx,.html,.css,.scss,.less,.sh,.bash,.zsh,.rs,.go,.java,.c,.cpp,.h,.hpp,.sql,.r,.rb,.php,.lua,.swift,.kt,.tf,.env,.gitignore,.dockerfile,.makefile,.conf,.cnf,.gradle,.properties,.lock,.pdf"
                onChange={e => handleFileUpload(e.target.files)}
                className="hidden"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={streaming || uploading}
                title="Upload files (images, documents, code)"
                className={`p-3 rounded-xl transition-colors ${
                  uploading ? 'bg-theme-purple/50' : 'bg-theme-bg-elevated hover:bg-theme-bg-hover'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {uploading ? <Loader2 className="w-5 h-5 animate-spin text-theme-purple" /> : <Paperclip className="w-5 h-5 text-theme-subtle" />}
              </button>
              <div className="flex-1 relative">
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={streaming ? 'Waiting for response...' : uploading ? 'Uploading...' : 'Type a message...'}
                  rows={1}
                  disabled={streaming || uploading}
                  className="w-full bg-theme-bg-elevated rounded-xl px-4 py-3 pr-12 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-theme-focus-ring/50 disabled:opacity-50"
                  onInput={e => {
                    const el = e.currentTarget
                    el.style.height = 'auto'
                    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
                  }}
                />
              </div>
              <VoiceButton
                onTranscribed={(text) => setInput(prev => prev + text)}
                disabled={streaming || uploading}
              />
              <button
                onClick={streaming ? handleCancel : handleSend}
                disabled={!streaming && !input.trim() && attachments.length === 0}
                className={`p-3 rounded-xl transition-colors ${
                  streaming ? 'bg-theme-danger hover:bg-theme-danger-hover' : 'bg-theme-accent hover:bg-theme-accent-hover disabled:bg-theme-bg-hover disabled:text-theme-muted'
                }`}
              >
                {streaming ? <Square className="w-5 h-5" /> : <Send className="w-5 h-5" />}
              </button>
            </div>
            <div className="flex items-center gap-3 mt-2 text-xs relative">
              {models.length > 0 && (
                <button
                  onClick={(e) => { e.stopPropagation(); setShowModelPickerFooter(!showModelPickerFooter) }}
                  className="flex items-center gap-1.5 px-2 py-1 bg-theme-bg-elevated hover:bg-theme-bg-hover rounded-lg transition-colors text-theme-subtle hover:text-theme-text"
                >
                  {models.find(m => m.id === selectedModelId)?.model_type === 'image'
                    ? <Image className="w-3 h-3 text-theme-purple" />
                    : <Bot className="w-3 h-3 text-theme-accent-text" />}
                  <span>{models.find(m => m.id === selectedModelId)?.name || 'Select'}</span>
                  <ChevronDown className="w-3 h-3" />
                </button>
              )}
              {models.length > 0 && models.find(m => m.id === selectedModelId)?.model_type === 'image' && (
                <select
                  value={imageGenSize}
                  onChange={e => setImageGenSize(e.target.value)}
                  className="bg-theme-bg-elevated rounded-lg px-2 py-1 text-xs text-theme-subtle border border-theme-border-light focus:outline-none focus:border-theme-purple"
                >
                  <option value="1024x1024">1024x1024</option>
                  <option value="1792x1024">1792x1024</option>
                  <option value="1024x1792">1024x1792</option>
                </select>
              )}
              {showModelPickerFooter && models.length > 0 && (
                    <div onClick={e => e.stopPropagation()} className="absolute bottom-full left-0 mb-1 w-64 bg-theme-bg-elevated border border-theme-border-light rounded-lg shadow-xl z-50 overflow-hidden">
                      {models.map(m => (
                        <button
                          key={m.id}
                          onClick={() => { setSelectedModelId(m.id); closeModelPickers() }}
                          className={`w-full text-left px-3 py-2 text-xs hover:bg-theme-bg-hover transition-colors flex items-center gap-2 ${m.id === selectedModelId ? 'bg-theme-bg-hover text-theme-accent-text' : 'text-theme-text-secondary'}`}
                        >
                          {m.model_type === 'image'
                            ? <Image className="w-3 h-3 shrink-0 text-theme-purple" />
                            : <Bot className="w-3 h-3 shrink-0 text-theme-accent-text" />}
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
        <div className="w-[420px] border-l border-theme-border-light bg-theme-bg-secondary flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 bg-theme-bg-elevated border-b border-theme-border-light shrink-0">
            <div className="flex items-center gap-2 text-xs min-w-0">
              <FileText className="w-3.5 h-3.5 text-theme-accent-text shrink-0" />
              <span className="font-mono text-theme-accent-dim truncate">{sidePanel.filename}.{sidePanel.format}</span>
              <span className="text-theme-muted shrink-0">({sidePanel.format.toUpperCase()})</span>
            </div>
            <button
              onClick={() => setSidePanel(null)}
              className="p-1 hover:bg-theme-bg-hover rounded transition-colors text-theme-muted hover:text-theme-text"
              title="Hide preview"
            >
              <Minus className="w-4 h-4" />
            </button>
          </div>
          <div className="flex-1 bg-theme-preview-bg">
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

      {/* Custom CSS Modal */}
      {showCustomCss && (
        <CustomCssPanel
          currentCss={cssPreviousRef.current}
          onClose={() => setShowCustomCss(false)}
          onSaved={(css) => { cssPreviousRef.current = css }}
        />
      )}

      {/* AI CSS Undo Toast */}
      {cssUndoToast && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 bg-theme-bg-elevated border border-theme-border-light rounded-xl px-4 py-3 shadow-2xl">
          <span className="text-sm text-theme-text">CSS updated by AI</span>
          <button
            onClick={async () => {
              injectUserCss(cssUndoToast.previousCss)
              try { await api.auth.css.save(cssUndoToast.previousCss) } catch {}
              cssPreviousRef.current = cssUndoToast.previousCss
              setCssUndoToast(null)
            }}
            className="px-3 py-1 text-sm font-medium bg-theme-accent hover:bg-theme-accent-hover rounded-lg"
          >
            Undo?
          </button>
          <button onClick={() => setCssUndoToast(null)} className="p-1 hover:bg-theme-bg-hover rounded text-theme-subtle">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  )
}

// --- Message Bubble ---

function MessageBubble({ message, msgIndex, messages, convId, onRegenerate, onCopy, copiedId, executingTools, expandedToolCalls, onToggleToolCall, setSidePanel }: {
  message: Message
  msgIndex: number
  messages: Message[]
  convId: number
  onRegenerate: (idx: number) => void
  onCopy: (content: string, id: number) => void
  copiedId: number | null
  executingTools: Set<string>
  expandedToolCalls: Set<string>
  onToggleToolCall: (id: string) => void
  setSidePanel: (panel: SidePanel | null) => void
}) {
  if (message.role === 'tool') {
    const isWebTool = message.tool_name === 'web_search' || message.tool_name === 'web_scrape'
    let matchedWebCall = false
    if (!isWebTool && message.tool_call_id) {
      for (let i = msgIndex - 1; i >= 0; i--) {
        const prev = messages[i]
        if (prev?.role === 'assistant' && prev.tool_calls_json) {
          const tc = prev.tool_calls_json.find(t => t.id === message.tool_call_id)
          if (tc) {
            if (tc.name === 'web_search' || tc.name === 'web_scrape') matchedWebCall = true
            break
          }
        }
      }
    }
    if (isWebTool || matchedWebCall) return null
  }

  const isUser = message.role === 'user'
  const isTool = message.role === 'tool'
  const isAssistant = message.role === 'assistant'
  const toolCalls = message.tool_calls_json
  const [thinkingExpanded, setThinkingExpanded] = useState(false)

  useEffect(() => {
    if (message.status === 'generating' && message.reasoning_content) {
      setThinkingExpanded(true)
    } else if (message.status !== 'generating' && message.status !== undefined) {
      setThinkingExpanded(false)
    }
  }, [message.status, message.reasoning_content])

  if (isTool && message.content) {
    const isHtmlRender = message.content.startsWith('HTML_RENDER:')
    const downloadMatch = message.content.match(/Download:\s*(\/api\/files\/\S+)/)
    if (isHtmlRender) {
      const html = atob(message.content.slice(12))
      return (
        <div>
          <div className="flex justify-start">
            <div className="max-w-full rounded-xl overflow-hidden border border-theme-border-light bg-theme-bg-secondary">
              <div className="flex items-center gap-2 px-3 py-2 bg-theme-bg-elevated text-xs text-theme-subtle border-b border-theme-border-light">
                <Eye className="w-3.5 h-3.5" /> HTML Preview
              </div>
              <iframe srcDoc={html} sandbox="allow-scripts" className="w-full h-96 bg-theme-preview-bg" title="HTML Preview" />
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
            <div className="max-w-3xl rounded-xl border border-theme-border-light bg-theme-bg-elevated overflow-hidden">
              <div className="flex items-center gap-2 px-3 py-2 bg-theme-bg-elevated text-xs text-theme-subtle border-b border-theme-border-light">
                <FileText className="w-3.5 h-3.5 text-theme-accent-text" />
                <span className="font-mono text-theme-accent-dim">{docFilename}.{docFormat}</span>
                <span className="text-theme-muted">({docFormat.toUpperCase()})</span>
              </div>
              {visualFormat ? (
                <div className="p-3 bg-theme-bg-secondary flex flex-col items-center gap-2">
                  <p className="text-xs text-theme-subtle">Document preview available</p>
                  <button
                    onClick={() => setSidePanel({
                      html: atob(htmlRenderMatch![1]),
                      filename: docFilename,
                      format: docFormat,
                    })}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-sm font-medium transition-colors cursor-pointer border-0"
                  >
                    <Eye className="w-4 h-4" />
                    Open Preview
                  </button>
                </div>
              ) : docContent ? (
                <div className="p-3 bg-theme-bg-secondary">
                  {isCode ? (
                    <MarkdownRenderer content={'```' + docFormat + '\n' + docContent.slice(0, 8000) + (docContent.length > 8000 ? '\n\n... (truncated)' : '') + '\n```'} />
                  ) : (
                    <pre className="text-xs text-theme-text-secondary whitespace-pre-wrap font-mono max-h-96 overflow-y-auto">
                      {docContent.length > 8000 ? docContent.slice(0, 8000) + '\n\n... (truncated)' : docContent}
                    </pre>
                  )}
                </div>
              ) : null}
              <div className="px-3 py-2 bg-theme-bg-elevated/50 text-xs text-theme-muted border-t border-theme-border-light/50 truncate">
                {message.content.replace(/\n?HTML_RENDER:[A-Za-z0-9+/=]+/, '').trim()}
              </div>
              {downloadMatch && (
                <div className="px-3 pb-2 bg-theme-bg-elevated/50">
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
                    className="inline-flex items-center gap-2 px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-xs font-medium transition-colors cursor-pointer border-0"
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
          <div className="max-w-2xl rounded-xl bg-theme-bg-elevated/50 border border-theme-border-light/50 px-4 py-2">
            <div className="flex items-center gap-2 text-xs text-theme-muted mb-1">
              {message.tool_name === 'web_search' && <Globe className="w-3 h-3" />}
              {message.tool_name === 'web_scrape' && <Search className="w-3 h-3" />}
              {message.tool_name === 'run_command' && <Terminal className="w-3 h-3" />}
              {message.tool_name === 'render_html' && <Eye className="w-3 h-3" />}
              <span className="font-mono">{message.tool_name}</span>
            </div>
            <pre className="text-xs text-theme-text-secondary whitespace-pre-wrap font-mono">
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
                className="mt-2 inline-flex items-center gap-2 px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-xs font-medium transition-colors cursor-pointer border-0"
              >
                <Download className="w-3.5 h-3.5" />
                Download {downloadMatch[1].split('/').pop()}
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (toolCalls && Array.isArray(toolCalls) && toolCalls.length > 0) {
    return (
      <div>
        {isAssistant && message.reasoning_content && (
          <div className="flex justify-start mb-1">
            <div className="w-8 shrink-0" />
            <div className="max-w-[75%] min-w-0">
              <button
                onClick={() => setThinkingExpanded(!thinkingExpanded)}
                className="flex items-center gap-1.5 text-xs text-theme-muted hover:text-theme-text transition-colors py-0.5 w-full"
              >
                {message.status === 'generating' ? (
                  <><Loader2 className="w-3 h-3 animate-spin text-theme-purple" /><span className="text-theme-purple">Thinking...</span></>
                ) : (
                  <><Brain className="w-3 h-3 text-theme-purple" /><span>Reasoning</span></>
                )}
                {thinkingExpanded ? <ChevronUp className="w-3 h-3 ml-auto" /> : <ChevronDown className="w-3 h-3 ml-auto" />}
              </button>
              {thinkingExpanded && (
                <div className="mt-1 rounded-xl bg-theme-bg-elevated/50 border border-theme-border-light/50 px-3 py-2 text-sm text-theme-subtle italic">
                  <MarkdownRenderer content={message.reasoning_content || ''} />
                </div>
              )}
            </div>
          </div>
        )}
        <div className="flex justify-start">
          {isAssistant && (
            <div className="w-8 h-8 rounded-full bg-theme-accent flex items-center justify-center mr-2 mt-0.5 shrink-0">
              <Bot className="w-4 h-4" />
            </div>
          )}
          <div className="max-w-[75%] min-w-0 space-y-2">
            <div className="flex flex-wrap gap-2">
              {toolCalls.map((tc, i) => {
                const isExecuting = executingTools.has(tc.id)
                const resultMsg = messages.find(m => m.role === 'tool' && m.tool_call_id === tc.id)
                const isCollapsibleWebCall = !isExecuting && (tc.name === 'web_search' || tc.name === 'web_scrape') && !!resultMsg
                const callExpanded = isCollapsibleWebCall && expandedToolCalls.has(tc.id)
                const summary = isExecuting
                  ? 'executing...'
                  : isCollapsibleWebCall
                    ? (() => {
                        const count = (resultMsg!.content!.match(/^\d+\.\s/gm) || []).length
                        return count > 0 ? `${count} result${count === 1 ? '' : 's'}` : 'done'
                      })()
                    : 'done'
                return (
                  <div key={i} className="flex flex-col">
                    <button
                      onClick={() => { if (isCollapsibleWebCall) onToggleToolCall(tc.id) }}
                      disabled={!isCollapsibleWebCall}
                      className={`flex items-center gap-2 px-3 py-2 bg-theme-bg-elevated rounded-lg border text-xs ${isExecuting ? 'border-theme-focus-ring/40 animate-pulse' : 'border-theme-border-light'} ${isCollapsibleWebCall ? 'cursor-pointer hover:bg-theme-bg-hover' : 'cursor-default'}`}
                    >
                      {isExecuting ? (
                        <Loader2 className="w-3.5 h-3.5 text-theme-accent-text animate-spin" />
                      ) : (
                        <Bot className="w-3.5 h-3.5 text-theme-accent-text" />
                      )}
                      <span className="font-mono text-theme-accent-dim">{tc.name}</span>
                      <span className={isExecuting ? 'text-theme-accent-text' : 'text-theme-muted'}>{summary}</span>
                      {isCollapsibleWebCall && (
                        callExpanded
                          ? <ChevronDown className="w-3 h-3 ml-1 text-theme-muted" />
                          : <ChevronRight className="w-3 h-3 ml-1 text-theme-muted" />
                      )}
                    </button>
                    {isCollapsibleWebCall && callExpanded && resultMsg && (
                      <div className="mt-1 rounded-xl bg-theme-bg-elevated/50 border border-theme-border-light/50 px-4 py-2 max-h-96 overflow-y-auto">
                        <pre className="text-xs text-theme-text-secondary whitespace-pre-wrap font-mono">
                          {resultMsg.content}
                        </pre>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
            {message.content && (
              <div className="bg-theme-bg-elevated rounded-xl px-4 py-2.5">
                <MarkdownRenderer content={message.content} />
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  const content = message.content || ''
  const hasHtmlRender = content.includes('HTML_RENDER:')

  let imageData: { images: string[]; revised_prompt?: string; prompt?: string; size?: string } | null = null
  if (isAssistant && content.trim().startsWith('{')) {
    try {
      const parsed = JSON.parse(content)
      if (parsed.images && Array.isArray(parsed.images)) {
        imageData = parsed
      }
    } catch {}
  }

  if (imageData) {
    return (
      <div>
        <div className="flex justify-start">
          <div className="max-w-[80%] bg-theme-bg-elevated rounded-xl px-4 py-3 space-y-3">
            <div className="flex items-center gap-2 text-xs text-theme-subtle">
              <Image className="w-3.5 h-3.5 text-theme-purple" />
              <span>Image Generation{imageData.size ? ` (${imageData.size})` : ''}</span>
            </div>
            {imageData.prompt && (
              <div className="text-xs text-theme-muted italic">Prompt: {imageData.prompt}</div>
            )}
            {imageData.revised_prompt && (
              <div className="text-xs text-theme-muted italic">Revised: {imageData.revised_prompt}</div>
            )}
            <div className="flex flex-wrap gap-2">
              {imageData.images.map((img, i) => (
                <div key={i} className="rounded-lg overflow-hidden border border-theme-border-light max-w-sm">
                  <img src={img} alt={`Generated ${i + 1}`} className="w-full object-contain" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (hasHtmlRender) {
    const parts = content.split(/(HTML_RENDER:[A-Za-z0-9+/=]+)/)
    return (
      <div>
        <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
          <div className={`max-w-[80%] ${isUser ? 'bg-theme-msg-user' : 'bg-theme-bg-elevated'} rounded-xl px-4 py-2`}>
            {parts.map((part, i) => {
              if (part.startsWith('HTML_RENDER:')) {
                try {
                  const html = atob(part.slice(12))
                  return (
                    <div key={i} className="my-2 rounded-lg overflow-hidden border border-theme-border-light">
                      <div className="flex items-center gap-2 px-3 py-1.5 bg-theme-bg-secondary text-xs text-theme-subtle border-b border-theme-border-light">
                        <Eye className="w-3 h-3" /> Preview
                      </div>
                      <iframe srcDoc={html} sandbox="allow-scripts" className="w-full h-96 bg-theme-preview-bg" title="Preview" />
                    </div>
                  )
                } catch { return <span key={i}>{part}</span> }
              }
              return <MarkdownRenderer key={i} content={part} />
            })}
          </div>
        </div>
        <div className={`flex gap-1 mt-0.5 ${isUser ? 'justify-end mr-10' : 'justify-start ml-10'}`}>
          <button onClick={() => onCopy(content, message.id)} className="p-1 hover:bg-theme-bg-hover rounded transition-colors text-theme-muted hover:text-theme-text" title="Copy">
            {copiedId === message.id ? <Check className="w-3.5 h-3.5 text-theme-accent-text" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
          {isUser && (
            <button onClick={() => onRegenerate(msgIndex)} className="p-1 hover:bg-theme-bg-hover rounded transition-colors text-theme-muted hover:text-theme-accent-text" title="Regenerate">
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div>
      {isUser && message.attachments_json && message.attachments_json.length > 0 && (
        <div className="flex justify-end mb-1">
          <div className="flex flex-wrap gap-1.5 mr-10">
            {message.attachments_json.map((att, i) => {
              const isImg = ['.png','.jpg','.jpeg','.gif','.webp','.bmp'].includes((att.file_type || '').toLowerCase())
              const token = localStorage.getItem('llmdash_token')
              const fileUrl = att.file_path.startsWith('/api/') ? att.file_path : null
              return (
                <div key={i} className="flex items-center gap-1.5 bg-theme-bg-elevated/80 rounded-lg px-2.5 py-1.5 text-xs border border-theme-border-light/50">
                  {isImg ? (
                    fileUrl ? (
                      <a href={fileUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 hover:opacity-80">
                        <Image className="w-3.5 h-3.5 text-theme-purple" />
                        <span className="text-theme-text-secondary truncate max-w-[120px]">{att.filename}</span>
                      </a>
                    ) : (
                      <>
                        <Image className="w-3.5 h-3.5 text-theme-purple" />
                        <span className="text-theme-text-secondary truncate max-w-[120px]">{att.filename}</span>
                      </>
                    )
                  ) : (
                    fileUrl ? (
                      <a href={fileUrl} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 hover:opacity-80">
                        <File className="w-3.5 h-3.5 text-theme-accent-text" />
                        <span className="text-theme-text-secondary truncate max-w-[120px]">{att.filename}</span>
                      </a>
                    ) : (
                      <>
                        <File className="w-3.5 h-3.5 text-theme-accent-text" />
                        <span className="text-theme-text-secondary truncate max-w-[120px]">{att.filename}</span>
                      </>
                    )
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
      {isAssistant && message.reasoning_content && (
        <div className="flex justify-start mb-1">
          <div className="w-8 shrink-0" />
          <div className="max-w-[75%] min-w-0">
            <button
              onClick={() => setThinkingExpanded(!thinkingExpanded)}
              className="flex items-center gap-1.5 text-xs text-theme-muted hover:text-theme-text transition-colors py-0.5 w-full"
            >
              {message.status === 'generating' ? (
                <><Loader2 className="w-3 h-3 animate-spin text-theme-purple" /><span className="text-theme-purple">Thinking...</span></>
              ) : (
                <><Brain className="w-3 h-3 text-theme-purple" /><span>Reasoning</span></>
              )}
              {thinkingExpanded ? <ChevronUp className="w-3 h-3 ml-auto" /> : <ChevronDown className="w-3 h-3 ml-auto" />}
            </button>
            {thinkingExpanded && (
              <div className="mt-1 rounded-xl bg-theme-bg-elevated/50 border border-theme-border-light/50 px-3 py-2 text-sm text-theme-subtle italic">
                <MarkdownRenderer content={message.reasoning_content || ''} />
              </div>
            )}
          </div>
        </div>
      )}
      <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
        {isAssistant && (
          <div className="w-8 h-8 rounded-full bg-theme-accent flex items-center justify-center mr-2 mt-0.5 shrink-0">
            <Bot className="w-4 h-4" />
          </div>
        )}
        <div className={`max-w-[75%] ${isUser ? 'bg-theme-msg-user' : 'bg-theme-bg-elevated'} rounded-xl px-4 py-2.5`}>
          <MarkdownRenderer content={content} />
        </div>
        {isUser && (
          <div className="w-8 h-8 rounded-full bg-theme-icon-user flex items-center justify-center ml-2 mt-0.5 shrink-0">
            <span className="text-xs font-bold">U</span>
          </div>
        )}
      </div>
      <div className={`flex gap-1 mt-0.5 ${isUser ? 'justify-end mr-10' : 'justify-start ml-10'}`}>
        <button onClick={() => onCopy(content, message.id)} className="p-1 hover:bg-theme-bg-hover rounded transition-colors text-theme-muted hover:text-theme-text" title="Copy">
          {copiedId === message.id ? <Check className="w-3.5 h-3.5 text-theme-accent-text" /> : <Copy className="w-3.5 h-3.5" />}
        </button>
        {isUser && (
          <button onClick={() => onRegenerate(msgIndex)} className="p-1 hover:bg-theme-bg-hover rounded transition-colors text-theme-muted hover:text-theme-accent-text" title="Regenerate">
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  )
}

export default App
