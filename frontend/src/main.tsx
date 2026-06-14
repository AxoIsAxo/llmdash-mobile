import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import 'highlight.js/styles/tokyo-night-dark.css'
import { BASE_THEME_CSS, DEFAULT_CSS } from './css-preset'

const baseStyle = document.createElement('style')
baseStyle.id = 'llmdash-base-css'
baseStyle.textContent = BASE_THEME_CSS
document.head.appendChild(baseStyle)

const styleEl = document.createElement('style')
styleEl.id = 'llmdash-user-css'
styleEl.textContent = DEFAULT_CSS
document.head.appendChild(styleEl)

function hexFromBg(bg: string): string | null {
  const parts = bg.split(/\s+/).map(Number)
  if (parts.length !== 3 || isNaN(parts[0])) return null
  return '#' + parts.map(p => Math.round(Math.min(255, Math.max(0, p))).toString(16).padStart(2, '0')).join('')
}

async function syncPwaTheme() {
  const root = document.documentElement
  const bg = getComputedStyle(root).getPropertyValue('--theme-bg').trim()
  if (!bg) return

  const hex = hexFromBg(bg)
  if (!hex) return

  localStorage.setItem('llmdash-pwa-theme', hex)

  const oldMeta = document.querySelector('meta[name="theme-color"]')
  if (oldMeta) oldMeta.remove()
  const meta = document.createElement('meta')
  meta.setAttribute('name', 'theme-color')
  meta.setAttribute('content', hex)
  document.head.appendChild(meta)

  try {
    const res = await fetch('/manifest.json?' + Date.now())
    const manifest = await res.json()
    manifest.theme_color = hex
    manifest.background_color = hex
    const blob = new Blob([JSON.stringify(manifest)], { type: 'application/manifest+json' })
    const url = URL.createObjectURL(blob)
    const link = document.querySelector<HTMLLinkElement>('link[rel="manifest"]')
    if (link) link.href = url
  } catch {}
}

;(window as any).__syncPwaTheme = syncPwaTheme

syncPwaTheme()

const userStyleObserver = new MutationObserver(syncPwaTheme)
userStyleObserver.observe(styleEl, { childList: true, characterData: true, subtree: true })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
