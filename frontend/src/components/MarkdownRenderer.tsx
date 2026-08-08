import React, { memo, useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import { Check, Copy, Download, Image, Play, Youtube } from 'lucide-react'

function CodeBlock({ language, children }: { language?: string; children: React.ReactNode }) {
  const [copied, setCopied] = useState(false)
  const codeRef = useRef<HTMLElement>(null)

  const handleCopy = useCallback(async () => {
    const text = codeRef.current?.textContent?.replace(/\n$/, '') ?? ''
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [])

  return (
    <div className="llm-code-block my-3 rounded-lg overflow-hidden border border-theme-border-light/50">
      <div className="flex items-center justify-between px-3 py-1.5 bg-theme-bg-secondary/80 text-xs text-theme-subtle">
        <span className="font-mono">{language || 'text'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-0.5 rounded hover:bg-theme-bg-active transition-colors"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-theme-accent-text" />
              <span className="text-theme-accent-text">Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className="p-4 overflow-x-auto bg-theme-code-bg text-sm">
        <code ref={codeRef} className={`hljs${language ? ` language-${language}` : ''}`}>{children}</code>
      </pre>
    </div>
  )
}

function preprocessSvgBlocks(markdown: string): string {
  return markdown.replace(/```svg\+?xml?\n?([\s\S]*?)```/g, (_, code) => {
    return code.trim()
  })
}

// --- P8: YouTube link previews ---

const YT_ID_RE = /^[A-Za-z0-9_-]{11}$/

export function youtubeIdFromUrl(url: string): string | null {
  try {
    const u = new URL(url)
    const host = u.hostname.replace(/^(www|m)\./, '')
    if (host === 'youtu.be') {
      const id = u.pathname.slice(1).split('/')[0] || ''
      return YT_ID_RE.test(id) ? id : null
    }
    if (host !== 'youtube.com') return null
    if (u.pathname === '/watch') {
      const v = u.searchParams.get('v')
      return v && YT_ID_RE.test(v) ? v : null
    }
    const m = u.pathname.match(/^\/(shorts|embed)\/([A-Za-z0-9_-]{11})/)
    return m ? m[2] : null
  } catch {
    return null
  }
}

// Bare-URL autolinking: wrap YouTube URLs that are NOT already inside markdown
// link syntax `(...)` or angle brackets `<...>` so react-markdown links them
// and the `a` override can render a preview card. Code spans (fenced blocks and
// inline backticks) are protected so code samples are never rewritten.
const BARE_URL_RE = /(?<![(<])(https?:\/\/[^\s<)"']+)/g
const CODE_SPAN_RE = /```[\s\S]*?```|`[^`\n]*`/g

export function preprocessYoutubeLinks(markdown: string): string {
  const protectedSpans: string[] = []
  const withoutCode = markdown.replace(CODE_SPAN_RE, m => {
    protectedSpans.push(m)
    return `\u0000${protectedSpans.length - 1}\u0000`
  })
  const linked = withoutCode.replace(BARE_URL_RE, m => (youtubeIdFromUrl(m) ? `<${m}>` : m))
  return linked.replace(/\u0000(\d+)\u0000/g, (_, i) => protectedSpans[Number(i)])
}

function YouTubeCard({ id }: { id: string }) {
  const [meta, setMeta] = useState<{ title: string; thumbnail: string; author: string } | null>(null)
  const [failed, setFailed] = useState(false)
  const [embed, setEmbed] = useState(false)

  useEffect(() => {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 6000)
    fetch(`https://www.youtube.com/oembed?url=${encodeURIComponent(`https://www.youtube.com/watch?v=${id}`)}&format=json`, { signal: ctrl.signal })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(d => setMeta({
        title: String(d.title || ''),
        thumbnail: String(d.thumbnail_url || ''),
        author: String(d.author_name || ''),
      }))
      .catch(() => setFailed(true))
      .finally(() => clearTimeout(timer))
    return () => { clearTimeout(timer); ctrl.abort() }
  }, [id])

  if (embed) {
    return (
      <div className="my-2 rounded-lg overflow-hidden border border-theme-border-light aspect-video">
        <iframe
          src={`https://www.youtube.com/embed/${id}?autoplay=1`}
          title="YouTube video player"
          className="w-full h-full"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
        />
      </div>
    )
  }

  const thumbnail = meta?.thumbnail || (failed ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : undefined)
  const title = meta?.title || (failed ? 'YouTube video' : '')
  const author = meta?.author || ''

  return (
    <a
      href={`https://www.youtube.com/watch?v=${id}`}
      target="_blank"
      rel="noopener noreferrer"
      onClick={e => { e.preventDefault(); setEmbed(true) }}
      className="block my-2 rounded-lg overflow-hidden border border-theme-border-light bg-theme-bg-elevated/50 group"
    >
      <div className="relative">
        {thumbnail ? (
          <img src={thumbnail} alt={title} className="w-full object-cover" style={{ maxHeight: 200 }} loading="lazy" />
        ) : (
          <div className="w-full aspect-video bg-theme-bg-active flex items-center justify-center text-theme-muted text-xs">Loading…</div>
        )}
        <span className="absolute inset-0 flex items-center justify-center">
          <span className="w-12 h-12 rounded-full bg-black/60 flex items-center justify-center group-hover:bg-black/80 transition-colors">
            <Play className="w-5 h-5 text-white fill-white" />
          </span>
        </span>
      </div>
      <div className="px-3 py-2">
        <p className="text-sm font-medium line-clamp-2">{title || 'YouTube video'}</p>
        {author && <p className="text-xs text-theme-muted mt-0.5">{author}</p>}
        <p className="text-xs text-theme-accent-text mt-1 flex items-center gap-1">
          <Youtube className="w-3.5 h-3.5" /> Watch on YouTube
        </p>
      </div>
    </a>
  )
}

interface MarkdownRendererProps {
  content: string
  youtubePreviewsEnabled?: boolean
}

const MarkdownRenderer = memo(function MarkdownRenderer({ content, youtubePreviewsEnabled = true }: MarkdownRendererProps) {
  const processed = preprocessYoutubeLinks(preprocessSvgBlocks(content))
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, rehypeSanitize, rehypeHighlight]}
      components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || '')
          const isBlock = match || className?.includes('hljs')
          const language = match ? match[1] : undefined

          if (isBlock) {
            return <CodeBlock language={language}>{children}</CodeBlock>
          }

          return (
            <code className="px-1.5 py-0.5 rounded-md bg-theme-bg-hover/50 text-xs font-mono text-theme-accent-dim" {...props}>
              {children}
            </code>
          )
        },
        pre({ children }) {
          return <>{children}</>
        },
        a({ href, children }) {
          const isDownload = typeof href === 'string' && href.startsWith('/api/files/')
          if (isDownload) {
            return (
              <button
                onClick={async () => {
                  try {
                    const token = localStorage.getItem('llmdash_token');
                    const headers: Record<string, string> = {};
                    if (token) headers['Authorization'] = `Bearer ${token}`;
                    const res = await fetch(href, { headers });
                    if (!res.ok) throw new Error(`Download failed: ${res.status}`);
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = href.split('/').pop() || 'download';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                  } catch (e) {
                    console.error('Download error:', e);
                  }
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-theme-accent hover:bg-theme-accent-hover rounded-lg text-xs font-medium text-white transition-colors cursor-pointer border-0"
              >
                <Download className="w-3.5 h-3.5" />
                {children}
              </button>
            )
          }
          if (youtubePreviewsEnabled && typeof href === 'string') {
            const ytId = youtubeIdFromUrl(href)
            if (ytId) {
              return <YouTubeCard id={ytId} />
            }
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-theme-accent-text hover:text-theme-accent-dim underline">
              {children}
            </a>
          )
        },
        p({ children }) {
          return <p className="my-1.5 leading-relaxed">{children}</p>
        },
        ul({ children }) {
          return <ul className="my-1.5 pl-5 list-disc space-y-0.5">{children}</ul>
        },
        ol({ children }) {
          return <ol className="my-1.5 pl-5 list-decimal space-y-0.5">{children}</ol>
        },
        blockquote({ children }) {
          return (
            <blockquote className="my-2 pl-3 border-l-2 border-theme-accent-text/50 text-theme-subtle italic">
              {children}
            </blockquote>
          )
        },
        table({ children }) {
          return (
            <div className="my-2 overflow-x-auto">
              <table className="w-full text-sm border-collapse border border-theme-border-light">
                {children}
              </table>
            </div>
          )
        },
        th({ children }) {
          return <th className="px-3 py-1.5 text-left bg-theme-bg-elevated border border-theme-border-light font-semibold">{children}</th>
        },
        td({ children }) {
          return <td className="px-3 py-1.5 border border-theme-border-light">{children}</td>
        },
        hr() {
          return <hr className="my-3 border-theme-border-light" />
        },
        h1({ children }) {
          return <h1 className="text-lg font-bold mt-4 mb-2">{children}</h1>
        },
        h2({ children }) {
          return <h2 className="text-base font-bold mt-3 mb-1.5">{children}</h2>
        },
        h3({ children }) {
          return <h3 className="text-sm font-semibold mt-2 mb-1">{children}</h3>
        },
      }}
    >
      {processed}
    </ReactMarkdown>
  )
})

export default MarkdownRenderer
