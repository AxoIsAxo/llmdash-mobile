import React, { memo, useCallback, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize from 'rehype-sanitize'
import { Check, Copy, Download, Image } from 'lucide-react'

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
    <div className="my-3 rounded-lg overflow-hidden border border-theme-border-light/50">
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

const MarkdownRenderer = memo(function MarkdownRenderer({ content }: { content: string }) {
  const processed = preprocessSvgBlocks(content)
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
      {content}
    </ReactMarkdown>
  )
})

export default MarkdownRenderer
