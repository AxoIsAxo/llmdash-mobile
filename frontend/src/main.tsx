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

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
