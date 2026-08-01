import React, { useState, useEffect, useRef } from 'react'
import { FileText, Download, Upload, History, X, Loader2, Eye, Trash2, ChevronDown, File as FileIcon, Plus, ExternalLink } from 'lucide-react'
import DOMPurify from 'dompurify'
import { api } from '../api'
import type { Document, DocumentVersion } from '../types'
import MarkdownRenderer from './MarkdownRenderer'

interface Props {
  currentUser: { id: number; role: string }
  onClose: () => void
}

export default function DocumentManager({ currentUser, onClose }: Props) {
  const [docs, setDocs] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null)
  const [versions, setVersions] = useState<DocumentVersion[]>([])
  const [showVersions, setShowVersions] = useState<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadDocs = async () => {
    setLoading(true)
    try {
      const data = await api.request<Document[]>('/documents')
      setDocs(data || [])
    } catch (e: any) {
      setError(e.message || 'Failed to load documents')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadDocs() }, [])

  const handleDownload = async (doc: Document) => {
    try {
      const token = api.getToken()
      const res = await fetch(`/api/documents/${doc.id}/download`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {}
      })
      if (!res.ok) throw new Error('Download failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${doc.filename}.${doc.format}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (e: any) {
      setError(e.message || 'Download failed')
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setError('')
    try {
      const token = api.getToken()
      const formData = new FormData()
      formData.append('file', file)
      const res = await fetch('/api/documents/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || 'Upload failed')
      }
      await loadDocs()
    } catch (e: any) {
      setError(e.message || 'Upload failed')
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this document?')) return
    try {
      await api.request(`/documents/${id}`, { method: 'DELETE' })
      setDocs(prev => prev.filter(d => d.id !== id))
      if (previewDoc?.id === id) setPreviewDoc(null)
    } catch (e: any) {
      setError(e.message || 'Delete failed')
    }
  }

  const loadVersions = async (docId: number) => {
    if (showVersions === docId) {
      setShowVersions(null)
      return
    }
    try {
      const data = await api.request<DocumentVersion[]>(`/documents/${docId}/versions`)
      setVersions(data || [])
      setShowVersions(docId)
    } catch (e: any) {
      setError(e.message || 'Failed to load versions')
    }
  }

  const formatDate = (s: string) => {
    const d = new Date(s)
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="fixed inset-0 bg-theme-overlay/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="llm-modal bg-theme-bg-secondary rounded-2xl w-full max-w-4xl max-h-[85vh] flex flex-col border border-theme-border-light" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-theme-border flex items-center justify-between shrink-0">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <FileText className="w-5 h-5 text-theme-accent-text" /> Documents
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="px-3 py-1.5 bg-theme-accent text-white rounded-lg text-sm hover:opacity-90 flex items-center gap-1.5"
            >
              <Upload className="w-4 h-4" /> Upload
            </button>
            <input ref={fileInputRef} type="file" accept=".md,.txt,.docx,.pdf,.odt,.tex,.py,.js,.ts,.html,.css,.json,.csv" className="hidden" onChange={handleUpload} />
            <button onClick={onClose} className="px-3 py-1.5 hover:bg-theme-bg-hover rounded-lg text-sm">Close</button>
          </div>
        </div>

        {error && (
          <div className="mx-4 mt-3 p-2 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 text-sm rounded-lg">{error}</div>
        )}

        <div className="flex-1 flex overflow-hidden">
          <div className={`${previewDoc ? 'w-1/2' : 'w-full'} overflow-y-auto p-4 border-r border-theme-border`}>
            {loading ? (
              <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-theme-muted" /></div>
            ) : docs.length === 0 ? (
              <div className="text-center py-12 text-theme-muted">
                <FileText className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p>No documents yet</p>
                <p className="text-sm mt-1">Upload a markdown file or ask the AI to create one</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-theme-muted border-b border-theme-border">
                    <th className="text-left py-2 px-3 font-medium">Filename</th>
                    <th className="text-left py-2 px-3 font-medium">Format</th>
                    <th className="text-left py-2 px-3 font-medium">Version</th>
                    <th className="text-left py-2 px-3 font-medium">Size</th>
                    <th className="text-left py-2 px-3 font-medium">Updated</th>
                    <th className="text-right py-2 px-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {docs.map(doc => (
                    <React.Fragment key={doc.id}>
                      <tr className="border-b border-theme-border/50 hover:bg-theme-bg-elevated/30">
                        <td className="py-2.5 px-3">
                          <button onClick={() => setPreviewDoc(previewDoc?.id === doc.id ? null : doc)} className="hover:text-theme-accent-text flex items-center gap-1.5">
                            <FileIcon className="w-4 h-4 shrink-0" />
                            <span className="truncate max-w-[200px]">{doc.filename}</span>
                          </button>
                        </td>
                        <td className="py-2.5 px-3 text-theme-muted uppercase text-xs">{doc.format}</td>
                        <td className="py-2.5 px-3 text-theme-muted">v{doc.version}</td>
                        <td className="py-2.5 px-3 text-theme-muted">{(doc.file_size / 1024).toFixed(1)} KB</td>
                        <td className="py-2.5 px-3 text-theme-muted text-xs">{formatDate(doc.updated_at)}</td>
                        <td className="py-2.5 px-3 text-right">
                          <div className="flex items-center justify-end gap-1">
                            <button onClick={() => handleDownload(doc)} className="p-1.5 hover:bg-theme-bg-hover rounded" title="Download">
                              <Download className="w-4 h-4" />
                            </button>
                            <button onClick={() => loadVersions(doc.id)} className="p-1.5 hover:bg-theme-bg-hover rounded" title="History">
                              <History className="w-4 h-4" />
                            </button>
                            <button onClick={() => handleDelete(doc.id)} className="p-1.5 hover:bg-red-100 dark:hover:bg-red-900/30 rounded text-red-500" title="Delete">
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                      {showVersions === doc.id && versions.length > 0 && (
                        <tr>
                          <td colSpan={6} className="px-3 pb-3">
                            <div className="bg-theme-bg-elevated rounded-lg p-3 ml-8">
                              <h4 className="text-xs font-semibold text-theme-muted mb-2 uppercase tracking-wider">Version History</h4>
                              {versions.map(v => (
                                <div key={v.version} className="flex items-center justify-between py-1.5 text-sm border-b border-theme-border/30 last:border-0">
                                  <span className="font-mono text-theme-accent-dim">v{v.version}</span>
                                  <span className="text-theme-muted text-xs">{formatDate(v.created_at)}</span>
                                  <span className="text-theme-muted text-xs">{(v.file_size / 1024).toFixed(1)} KB</span>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {previewDoc && (
            <div className="w-1/2 overflow-y-auto p-4 bg-white dark:bg-gray-900">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold truncate">{previewDoc.filename}.{previewDoc.format}</h3>
                <div className="flex items-center gap-1">
                  <button onClick={() => handleDownload(previewDoc)} className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded" title="Download">
                    <Download className="w-4 h-4" />
                  </button>
                  <button onClick={() => setPreviewDoc(null)} className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded">
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>
              {previewDoc.format === 'html' ? (
                <div className="prose dark:prose-invert max-w-none text-sm" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(previewDoc.content || '') }} />
              ) : previewDoc.format === 'md' ? (
                <div className="prose dark:prose-invert max-w-none text-sm">
                  <MarkdownRenderer content={previewDoc.content || ''} />
                </div>
              ) : (
                <pre className="text-xs text-theme-muted whitespace-pre-wrap">{previewDoc.content?.slice(0, 5000)}</pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}