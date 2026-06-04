import { Capacitor } from '@capacitor/core'
import { Preferences } from '@capacitor/preferences'

const TOKEN_KEY = 'llmdash_token'

let cachedToken: string | null = null
let initPromise: Promise<void> | null = null

export function initStorage(): Promise<void> {
  if (!initPromise) {
    initPromise = (async () => {
      if (Capacitor.isNativePlatform()) {
        const { value } = await Preferences.get({ key: TOKEN_KEY })
        cachedToken = value
      } else {
        cachedToken = localStorage.getItem(TOKEN_KEY)
      }
    })()
  }
  return initPromise
}

export function getToken(): string | null {
  return cachedToken
}

export async function setToken(token: string | null): Promise<void> {
  cachedToken = token
  if (Capacitor.isNativePlatform()) {
    if (token) await Preferences.set({ key: TOKEN_KEY, value: token })
    else await Preferences.remove({ key: TOKEN_KEY })
    return
  }
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}
