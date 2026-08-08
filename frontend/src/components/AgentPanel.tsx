import React, { useState } from 'react'
import { FileText, Brain, Palette, GitBranch, X, Sparkles } from 'lucide-react'
import DocumentManager from './DocumentManager'
import MemoryPanel from './MemoryPanel'
import CustomCssPanel from './CustomCssPanel'
import GitPanel from './GitPanel'
import type { User } from '../types'

interface Props {
  currentUser: User
  onClose: () => void
  currentCss: string
  onCssSaved: (css: string) => void
}

type AgentTab = 'documents' | 'memory' | 'css' | 'git'

export default function AgentPanel({ currentUser, onClose, currentCss, onCssSaved }: Props) {
  const [tab, setTab] = useState<AgentTab>('documents')
  const isAdmin = currentUser.role === 'owner' || currentUser.role === 'admin'

  const tabs = [
    { key: 'documents' as AgentTab, icon: FileText, label: 'Documents' },
    { key: 'memory' as AgentTab, icon: Brain, label: 'Memory' },
    { key: 'css' as AgentTab, icon: Palette, label: 'Appearance' },
    { key: 'git' as AgentTab, icon: GitBranch, label: 'Git' },
  ]

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="llm-modal bg-theme-bg-secondary rounded-2xl w-full max-w-5xl max-h-[85vh] flex flex-col border border-theme-border-light" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-theme-border flex items-center justify-between shrink-0">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-theme-accent-text" /> Agent
          </h2>
          <button onClick={onClose} className="px-3 py-1.5 hover:bg-theme-bg-hover rounded-lg text-sm">Close</button>
        </div>
        <div className="flex border-b border-theme-border shrink-0">
          {tabs.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 py-2.5 text-sm font-medium flex items-center justify-center gap-1.5 transition-colors ${
                tab === t.key ? 'text-theme-accent-text border-b-2 border-theme-accent-text bg-theme-bg-elevated/50' : 'text-theme-muted hover:text-theme-text'
              }`}
            >
              <t.icon className="w-4 h-4" /> {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {tab === 'documents' && <DocumentManager currentUser={currentUser} onClose={onClose} embedded />}
          {tab === 'memory' && <MemoryPanel currentUser={currentUser} onClose={onClose} embedded />}
          {tab === 'css' && <CustomCssPanel currentCss={currentCss} onClose={onClose} onSaved={onCssSaved} embedded />}
          {tab === 'git' && <GitPanel isAdmin={isAdmin} />}
        </div>
      </div>
    </div>
  )
}
