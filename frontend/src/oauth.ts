import { Capacitor } from '@capacitor/core'

const OAUTH_SCHEME = 'llmdash-oauth'

/**
 * Start the Extrovert OIDC flow.
 *
 * Web: plain page navigation — the server callback stores the JWT in this
 * origin's localStorage and redirects back to "/".
 *
 * Android: Capacitor hands any http(s) URL whose host isn't the app's own
 * origin to the OS browser, so a plain navigation would complete the login
 * in the browser and the app would never receive the token. Instead we
 * route through the custom `llmdash-oauth://` scheme: the native
 * WebViewClient in MainActivity keeps the whole round-trip inside the
 * WebView, captures the JWT from the server callback page, stores it in
 * @capacitor/preferences, and returns to the bundled app UI.
 */
export function startExtrovertOAuth(authorizeUrl: string): void {
  if (Capacitor.getPlatform() !== 'android') {
    window.location.href = authorizeUrl
    return
  }

  let target: URL
  try {
    target = new URL(authorizeUrl)
  } catch {
    // Not a URL we can route — fall back to the (browser) default.
    window.location.href = authorizeUrl
    return
  }
  if (target.protocol !== 'https:' && target.protocol !== 'http:') {
    window.location.href = authorizeUrl
    return
  }

  // The server callback page host is where the JWT will land; tell native
  // which origin to watch for it.
  let server = ''
  const redirectUri = target.searchParams.get('redirect_uri')
  if (redirectUri) {
    try {
      server = new URL(redirectUri).origin
    } catch {
      /* keep empty — native will then only match the callback path heuristics */
    }
  }

  const params = new URLSearchParams({ target: authorizeUrl })
  if (server) params.set('server', server)
  window.location.href = `${OAUTH_SCHEME}://start?${params.toString()}`
}
