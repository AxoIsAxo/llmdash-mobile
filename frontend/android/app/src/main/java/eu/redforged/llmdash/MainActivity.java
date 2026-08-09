package eu.redforged.llmdash;

import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;

/**
 * Android entry point.
 *
 * Also wires up in-app handling for the Extrovert OIDC login:
 *
 * Capacitor hands every http(s) URL whose host is not the app's own origin
 * (and not allow-listed) to the OS browser. So a plain
 * `window.location.href = <extrovert-authorize-url>` would run the whole
 * OIDC round-trip in the browser, where the server callback stores the JWT
 * in the browser's localStorage — the app would never see the token
 * ("logs me in in the browser").
 *
 * Instead the frontend (frontend/src/oauth.ts) routes the flow through the
 * custom `llmdash-oauth://start?target=...&server=...` scheme. This
 * WebViewClient intercepts it and keeps every navigation of the round-trip
 * inside the WebView; the OIDC callback itself is fetched natively (the JWT
 * is inlined in its HTML), stored under the same SharedPreferences group
 * (@capacitor/preferences default: "CapacitorStorage") that the app's
 * storage.ts reads, before returning to the bundled app UI.
 */
public class MainActivity extends BridgeActivity {

    private static final String OAUTH_SCHEME = "llmdash-oauth";
    private static final String TOKEN_PREFS = "CapacitorStorage";
    private static final String TOKEN_KEY = "llmdash_token";
    private static final long OAUTH_TIMEOUT_MS = 10 * 60 * 1000L;
    private static final String LOG_TAG = "LlmdashOAuth";

    /**
     * Extrovert's consent page renders its hidden _csrf field empty on a fresh
     * session (the server generates the CSRF token lazily — only when some page
     * of the session renders a CSRF form — and the session is regenerated at
     * login), so clicking "Authorize" POSTs _csrf= and the provider answers
     * "CSRF token missing or invalid. Re-open the authorization request.".
     * This fetches a same-origin page that renders a CSRF form (the homepage),
     * which makes the server mint the token for this session, then fills the
     * consent form with it.
     *
     * The submit is intercepted and done via fetch(), so the WebView itself
     * never POSTs and never loads the callback page (whose JS redirects to
     * "/" — the web SPA — which on Android boots with empty native prefs and
     * shows the web login page instead of handing the token to the app).
     * fetch() follows the 302 to the callback and reads the response text
     * (the LLMDash server sends ACAO:*; default same-origin credentials keep
     * the wildcard CORS response readable); the JWT — or the error the server
     * rendered — is passed to native via the llmdash-oauth://token or
     * llmdash-oauth://error scheme, which stores it and ends the flow.
     */
    private static final String CONSENT_CSRF_JS =
            "(function(){"
            + "if(window.__llmdashCsrfPrimed)return;window.__llmdashCsrfPrimed=true;"
            + "function findForm(){"
            + "var f=document.querySelector('form[action=\"/api/v1/oauth/authorize\"]');"
            + "if(f)return f;"
            + "var fs=document.querySelectorAll('form');"
            + "for(var i=0;i<fs.length;i++){"
            + "if(fs[i].querySelector('input[name=\"approve\"]'))return fs[i];"
            + "}"
            + "return null;"
            + "}"
            + "var form=findForm();"
            + "if(!form)return;"
            + "var input=form.querySelector('input[name=\"_csrf\"]');"
            + "if(!input)return;"
            + "var submitting=false;"
            + "function fill(){"
            + "if(input.value)return Promise.resolve();"
            + "return fetch('/',{credentials:'include'})"
            + ".then(function(r){return r.text();})"
            + ".then(function(h){"
            + "var m=h.match(/name=\"_csrf\" value=\"([0-9a-f]+)\"/);"
            + "if(m)input.value=m[1];"
            + "})"
            + ".catch(function(){});"
            + "}"
            + "function submitConsent(){"
            + "if(submitting)return;submitting=true;"
            + "fill().then(function(){"
            + "var action=form.getAttribute('action')||'/api/v1/oauth/authorize';"
            + "/* default credentials (same-origin) so the cross-origin callback"
            + " * response stays readable with ACAO:* — credentials:'include'"
            + " * would make the browser reject the wildcard CORS response */"
            + "return fetch(action,{method:'POST',body:new FormData(form)});"
            + "}).then(function(r){return r.text();}).then(function(html){"
            + "var m=html.match(/localStorage\\.setItem\\('llmdash_token', \"([^\"]+)\"\\)/);"
            + "if(m){window.location.href='llmdash-oauth://token?value='+encodeURIComponent(m[1]);return;}"
            + "var e=html.match(/<p style=\"color:#ff5d6c\">([^<]+)<\\/p>/);"
            + "if(e){window.location.href='llmdash-oauth://error?msg='+encodeURIComponent(e[1].trim());return;}"
            + "window.location.href='llmdash-oauth://error?msg='+encodeURIComponent('unexpected callback response');"
            + "}).catch(function(){submitting=false;});"
            + "}"
            + "form.addEventListener('submit',function(e){"
            + "e.preventDefault();"
            + "submitConsent();"
            + "});"
            + "if(!input.value)fill();"
            + "})();";

    private LlmdashWebViewClient webViewClient;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webViewClient = new LlmdashWebViewClient();
        getBridge().setWebViewClient(webViewClient);
    }

    private final class LlmdashWebViewClient extends BridgeWebViewClient {

        private volatile boolean oauthMode = false;
        private volatile String oauthServerHost = null;
        private volatile String oauthProviderHost = null;
        private volatile long oauthStartedAt = 0L;

        LlmdashWebViewClient() {
            super(getBridge());
        }

        @Override
        public android.webkit.WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            // The OIDC callback must be fetched EXACTLY once (the server pops the
            // one-time state on first request). Intercept it before the WebView
            // sends it: fetch it natively, extract the JWT the server inlines in
            // the HTML, store it in the app's prefs, and return a placeholder
            // page so the WebView never renders the callback / its "/" redirect.
            if (oauthMode && oauthServerHost != null && oauthServerHost.equals(request.getUrl().getHost())
                    && request.getUrl().toString().contains("/api/auth/extrovert/callback")
                    && request.isForMainFrame()) {
                final String callbackUrl = request.getUrl().toString();
                view.post(() -> handleCallbackNatively(view, callbackUrl));
                return new android.webkit.WebResourceResponse("text/html", "UTF-8",
                        new java.io.ByteArrayInputStream("<html><body>Loading…</body></html>".getBytes(java.nio.charset.StandardCharsets.UTF_8)));
            }
            return super.shouldInterceptRequest(view, request);
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            Log.d(LOG_TAG, "shouldOverride(request) " + url + " oauthMode=" + oauthMode);
            if (OAUTH_SCHEME.equals(url.getScheme())) {
                handleOAuthScheme(view, url);
                return true;
            }
            if (oauthMode && isExpired()) {
                endOAuthFlow(view);
                return true;
            }
            if (oauthMode && isHttp(url)) {
                // Keep the whole OIDC round-trip inside the WebView. The
                // consent page submits via fetch() and hands the result to us
                // through the llmdash-oauth:// scheme, so the callback page is
                // never loaded by the WebView; the onPageFinished localStorage
                // read stays as a fallback.
                return false;
            }
            return super.shouldOverrideUrlLoading(view, request);
        }

        @Override
        @SuppressWarnings("deprecation")
        public boolean shouldOverrideUrlLoading(WebView view, String urlString) {
            Log.d(LOG_TAG, "shouldOverride(String) " + urlString + " oauthMode=" + oauthMode);
            Uri url = Uri.parse(urlString);
            if (OAUTH_SCHEME.equals(url.getScheme())) {
                handleOAuthScheme(view, url);
                return true;
            }
            if (oauthMode && isExpired()) {
                endOAuthFlow(view);
                return true;
            }
            if (oauthMode && isHttp(url)) {
                return false;
            }
            return super.shouldOverrideUrlLoading(view, urlString);
        }

        /** Route the llmdash-oauth:// scheme: start, token (JWT from the consent fetch), error, or legacy callback. */
        private void handleOAuthScheme(WebView view, Uri url) {
            String host = url.getHost();
            if ("token".equals(host)) {
                String v = url.getQueryParameter("value");
                if (v != null && v.split("\\.").length == 3) {
                    Log.d(LOG_TAG, "token from consent fetch, len " + v.length());
                    storeToken(v);
                    toast("Signed in via Extrovert ✓");
                } else {
                    Log.d(LOG_TAG, "invalid token from consent fetch");
                    toast("Extrovert login failed: invalid token from callback");
                }
                endOAuthFlow(view);
            } else if ("error".equals(host)) {
                String msg = url.getQueryParameter("msg");
                if (msg != null && !msg.isEmpty()) {
                    copyToClipboard("LLMDash OAuth error", msg);
                    toast("Extrovert login failed: " + msg);
                }
                endOAuthFlow(view);
            } else if ("callback".equals(host)) {
                handleOAuthCallback(view, url);
            } else {
                startOAuthFlow(view, url);
            }
        }

        /** llmdash-oauth://callback?url=<oidc callback URL> — handed over by the consent page's script. */
        private void handleOAuthCallback(WebView view, Uri url) {
            String cb = url.getQueryParameter("url");
            if (cb == null || !isHttp(Uri.parse(cb))) {
                Log.d(LOG_TAG, "invalid callback URL from consent page");
                endOAuthFlow(view);
                return;
            }
            // Only fetch callbacks for the LLMDash server this flow talks to.
            if (oauthServerHost == null || !oauthServerHost.equals(Uri.parse(cb).getHost())) {
                Log.d(LOG_TAG, "callback host mismatch: " + Uri.parse(cb).getHost());
                endOAuthFlow(view);
                return;
            }
            handleCallbackNatively(view, cb);
        }

        private void startOAuthFlow(WebView view, Uri url) {
            // llmdash-oauth://start?target=<extrovert authorize url>&server=<server origin>
            Log.d(LOG_TAG, "startOAuthFlow target=" + url.getQueryParameter("target") + " server=" + url.getQueryParameter("server"));
            if (oauthMode) {
                if (isExpired()) {
                    // The previous flow timed out (e.g. stranded on a provider
                    // error page) — tear it down and fall through to restart.
                    endOAuthFlow(view);
                } else {
                    // A flow is already running — ignore re-entrant callbacks
                    // (double-tap, both shouldOverrideUrlLoading overloads) so
                    // we never fire two parallel authorize requests.
                    return;
                }
            }
            String target = url.getQueryParameter("target");
            if (target == null || !"https".equals(Uri.parse(target).getScheme())) {
                return; // only https authorize URLs are routed through the WebView
            }
            oauthProviderHost = Uri.parse(target).getHost();

            // Derive the LLMDash server host from the authorize URL's own
            // redirect_uri (where the JWT will land) — never trust a client
            // supplied server= param for deciding which host to fetch.
            String redirectUri = Uri.parse(target).getQueryParameter("redirect_uri");
            oauthServerHost = redirectUri != null ? Uri.parse(redirectUri).getHost() : null;

            // Start from a clean slate. The provider's session cookie
            // (connect.sid) persists in the WebView across app restarts and
            // retries; once it holds a stale session, the authorize endpoint
            // rejects the request with "CSRF token missing or invalid. Re-open
            // the authorization request." (the CSRF token is only issued to a
            // fresh login-page session, and the login redirect echoes it back
            // into the authorize URL). Clearing cookies for the provider and
            // the LLMDash server origin forces every attempt through the login
            // form again, so the CSRF pairing is always intact.
            clearCookiesFor(target);
            if (oauthServerHost != null) {
                clearCookiesFor("https://" + oauthServerHost + "/");
            }

            oauthMode = true;
            oauthStartedAt = System.currentTimeMillis();
            final String finalTarget = target;
            view.post(() -> view.loadUrl(finalTarget));
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            super.onPageStarted(view, url, favicon);
            Log.d(LOG_TAG, "onPageStarted " + url + " host=" + safeHost(url) + " oauthMode=" + oauthMode);

            // If the user backs out of the login flow, we land back on the
            // app's own origin — stop treating the WebView as an OAuth shell.
            String appHost = appHost();
            if (oauthMode && appHost != null && appHost.equals(safeHost(url))) {
                oauthMode = false;
                oauthServerHost = null;
                oauthProviderHost = null;
                oauthStartedAt = 0L;
            }
        }

        /**
         * Fetch the OIDC callback URL from native code, extract the JWT the
         * server inlines into the page HTML, store it in the same prefs the
         * app reads, and return to the bundled app UI. No WebView JS needed.
         */
        private void handleCallbackNatively(WebView view, String callbackUrl) {
            Log.d(LOG_TAG, "handling callback natively: " + callbackUrl);
            oauthMode = false; // flow is being finished here — ignore further WebView events
            oauthServerHost = null;
            oauthProviderHost = null;
            oauthStartedAt = 0L;
            view.stopLoading();
            new Thread(() -> {
                final CallbackResult res = fetchCallbackResult(callbackUrl);
                runOnUiThread(() -> {
                    if (res.token != null) {
                        Log.d(LOG_TAG, "callback token len " + res.token.length());
                        storeToken(res.token);
                        toast("Signed in via Extrovert ✓");
                    } else {
                        Log.d(LOG_TAG, "callback returned no token: " + res.error);
                        copyToClipboard("LLMDash OAuth error", res.error);
                        String e = res.error.length() > 120 ? res.error.substring(0, 120) : res.error;
                        toast("Extrovert login failed: " + e + " (full error copied to clipboard)");
                    }
                    try {
                        view.loadUrl(homeUrl());
                    } catch (Exception e) {
                        Log.d(LOG_TAG, "reload home failed: " + e);
                    }
                });
            }).start();
        }

        /** Result of fetching the callback: a JWT, or the error the server rendered. */
        private static final class CallbackResult {
            final String token;
            final String error;
            CallbackResult(String token, String error) {
                this.token = token;
                this.error = error;
            }
        }

        /** GET the callback URL: extract the JWT, or the error text, from its HTML. */
        private CallbackResult fetchCallbackResult(String callbackUrl) {
            try {
                java.net.URL u = new java.net.URL(callbackUrl);
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) u.openConnection();
                conn.setInstanceFollowRedirects(true);
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(15000);
                String cookies = CookieManager.getInstance().getCookie(callbackUrl);
                if (cookies != null && !cookies.isEmpty()) {
                    conn.setRequestProperty("Cookie", cookies);
                }
                int code = conn.getResponseCode();
                java.io.InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
                String body = readFully(is);
                java.util.regex.Matcher m = java.util.regex.Pattern
                        .compile("setItem\\('llmdash_token', \"([^\"]+)\"\\)")
                        .matcher(body);
                if (m.find()) {
                    String token = m.group(1);
                    // Only accept a well-formed JWT (header.payload.signature).
                    if (token.split("\\.").length == 3) {
                        return new CallbackResult(token, null);
                    }
                }
                // Error page — pull out the message the server rendered.
                java.util.regex.Matcher em = java.util.regex.Pattern
                        .compile("<p style=\"color:#ff5d6c\">([^<]+)</p>")
                        .matcher(body);
                String err = em.find() ? em.group(1).trim() : "no token in callback page";
                return new CallbackResult(null, err);
            } catch (Exception e) {
                Log.d(LOG_TAG, "callback fetch failed: " + e);
                return new CallbackResult(null, "callback request failed: " + e);
            }
        }

        private void toast(String msg) {
            android.widget.Toast.makeText(MainActivity.this, msg, android.widget.Toast.LENGTH_LONG).show();
        }

        /** Put text on the system clipboard so a long error can be read/pasted in full. */
        private void copyToClipboard(String label, String text) {
            try {
                android.content.ClipboardManager cm =
                        (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                cm.setPrimaryClip(android.content.ClipData.newPlainText(label, text));
            } catch (Exception e) {
                Log.d(LOG_TAG, "clipboard copy failed: " + e);
            }
        }

        /** Read an InputStream to a UTF-8 string (works on API 23+, unlike readAllBytes). */
        private static String readFully(java.io.InputStream is) throws java.io.IOException {
            if (is == null) return "";
            java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = is.read(buf)) != -1) bos.write(buf, 0, n);
            return new String(bos.toByteArray(), java.nio.charset.StandardCharsets.UTF_8);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
            Log.d(LOG_TAG, "onPageFinished " + url + " host=" + safeHost(url) + " oauthMode=" + oauthMode
                    + " title=" + view.getTitle());
            if (!oauthMode) return;
            if (isExpired()) {
                endOAuthFlow(view);
                return;
            }

            String host = safeHost(url);
            if (oauthProviderHost != null && oauthProviderHost.equals(host)
                    && url.contains("/api/v1/oauth/authorize")) {
                // Provider consent page — fill its missing CSRF token and keep waiting.
                primeConsentCsrf(view);
                return;
            }

            if (oauthServerHost == null || !oauthServerHost.equals(host)) {
                return; // still on the OIDC provider or an unrelated page — keep waiting
            }

            // We are on the LLMDash server. The callback page stores the JWT in
            // localStorage on this origin (and the redirect target "/" inherits
            // it, same origin) — read it natively and hand it to the app. If
            // there is no token, the callback returned an error page — pull its
            // visible text so the failure is shown on screen (e.g. "Account
            // limit reached for this IP address.", "Login expired. ...").
            final String pageUrl = url;
            view.evaluateJavascript(
                    "(function(){ try { var t = localStorage.getItem('" + TOKEN_KEY + "');"
                            + " if (t) return 'TOKEN:' + t;"
                            + " var b = document.body ? document.body.innerText.replace(/\\s+/g,' ').trim() : '';"
                            + " return 'ERR:' + b; } catch(e) { return 'ERR:' + e; } })()",
                    (ValueCallback<String>) value -> {
                        String v = unquoteJson(value);
                        String token = null;
                        String err = null;
                        if (v != null && v.startsWith("TOKEN:")) {
                            token = v.substring("TOKEN:".length());
                        } else if (v != null && v.startsWith("ERR:")) {
                            err = v.substring("ERR:".length());
                        }
                        Log.d(LOG_TAG, "server page read: " + (token != null ? "token len " + token.length() : "err: " + err)
                                + " on " + pageUrl);
                        if (token != null && !token.isEmpty()) {
                            storeToken(token);
                            Log.d(LOG_TAG, "token stored — ending flow (logged in)");
                            endOAuthFlow(view);
                        } else if (pageUrl.contains("/api/auth/extrovert/callback")) {
                            // Error / expired / cancelled login — drop back into the app.
                            String full = (err != null && !err.trim().isEmpty()) ? err.trim() : "no token in callback page";
                            copyToClipboard("LLMDash OAuth error", full);
                            String msg = full.length() > 120 ? full.substring(0, 120) : full;
                            Log.d(LOG_TAG, "callback page without token: " + full);
                            toast("Extrovert login failed: " + msg + " (full error copied to clipboard)");
                            endOAuthFlow(view);
                        }
                    });
        }

        private boolean isExpired() {
            return oauthStartedAt > 0L && System.currentTimeMillis() - oauthStartedAt > OAUTH_TIMEOUT_MS;
        }

        private void endOAuthFlow(WebView view) {
            oauthMode = false;
            oauthServerHost = null;
            oauthProviderHost = null;
            oauthStartedAt = 0L;
            view.post(() -> view.loadUrl(homeUrl()));
        }

        private void primeConsentCsrf(WebView view) {
            view.evaluateJavascript(CONSENT_CSRF_JS, null);
        }

        private boolean isHttp(Uri url) {
            String s = url.getScheme();
            return "http".equals(s) || "https".equals(s);
        }

        /**
         * Expire every cookie the WebView currently holds for the given origin
         * (a full http(s) URL). CookieManager has no per-domain clear, so read
         * the cookie header and overwrite each name with an expired value.
         * Best-effort: never block the login flow on this.
         */
        private void clearCookiesFor(String url) {
            try {
                CookieManager cm = CookieManager.getInstance();
                String cookies = cm.getCookie(url);
                if (cookies == null || cookies.isEmpty()) {
                    return;
                }
                for (String part : cookies.split(";")) {
                    String name = part.split("=", 2)[0].trim();
                    if (name.isEmpty()) {
                        continue;
                    }
                    // Path=/ expiry replaces the stored cookie for the same
                    // name + host (the provider's connect.sid is Path=/).
                    cm.setCookie(url, name + "=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT");
                    cm.setCookie(url, name + "=; Path=/; Max-Age=0");
                }
                cm.flush();
            } catch (Exception e) {
                // ignore — next attempt still goes through the login page
            }
        }
    }

    private String appHost() {
        String app = getBridge().getAppUrl();
        return app != null ? Uri.parse(app).getHost() : null;
    }

    private static String safeHost(String url) {
        try {
            return Uri.parse(url).getHost();
        } catch (Exception e) {
            return null;
        }
    }

    private String homeUrl() {
        String app = getBridge().getAppUrl();
        if (app == null) app = "https://localhost";
        return app.endsWith("/") ? app : app + "/";
    }

    private void storeToken(String token) {
        SharedPreferences prefs = getSharedPreferences(TOKEN_PREFS, MODE_PRIVATE);
        prefs.edit().putString(TOKEN_KEY, token).apply();
    }

    /** evaluateJavascript returns a JSON-encoded value ("..." or null). */
    private static String unquoteJson(String value) {
        if (value == null || "null".equals(value)) return null;
        if (value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")) {
            return value.substring(1, value.length() - 1)
                    .replace("\\\"", "\"")
                    .replace("\\\\", "\\");
        }
        return value;
    }
}
