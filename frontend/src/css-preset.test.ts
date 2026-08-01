import { describe, expect, it } from 'vitest'
import { BASE_THEME_CSS, DEFAULT_CSS } from './css-preset'

describe('css-preset', () => {
  it('exposes a root theme stylesheet', () => {
    expect(BASE_THEME_CSS).toContain(':root')
    expect(DEFAULT_CSS).toContain('--theme-bg')
  })

  it('defines all tokens used by tailwind config', () => {
    for (const token of ['--theme-accent', '--theme-danger', '--theme-radius', '--theme-z-modal', '--theme-spin-duration']) {
      expect(BASE_THEME_CSS).toContain(token)
    }
  })

  it('defines the layout tokens used by the theme system', () => {
    for (const token of ['--theme-bubble-max-width', '--theme-side-panel-width', '--theme-avatar-size', '--theme-message-gap', '--theme-preview-height', '--theme-auth-max-width']) {
      expect(BASE_THEME_CSS).toContain(token)
    }
  })
})
