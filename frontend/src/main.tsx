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

function syncThemeColor() {
  const root = document.documentElement
  const bg = getComputedStyle(root).getPropertyValue('--theme-bg').trim()
  if (!bg) return
  let meta = document.querySelector('meta[name="theme-color"]')
  if (!meta) {
    meta = document.createElement('meta')
    meta.setAttribute('name', 'theme-color')
    document.head.appendChild(meta)
  }
  const parts = bg.split(/\s+/).map(Number)
  if (parts.length === 3 && !isNaN(parts[0])) {
    meta.setAttribute('content', `rgb(${parts[0]},${parts[1]},${parts[2]})`)
  }
}

;(window as any).__syncPwaTheme = syncThemeColor

syncThemeColor()

const userStyleObserver = new MutationObserver(syncThemeColor)
userStyleObserver.observe(styleEl, { childList: true, characterData: true, subtree: true })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
