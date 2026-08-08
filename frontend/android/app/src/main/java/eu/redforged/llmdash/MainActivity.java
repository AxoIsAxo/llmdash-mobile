package eu.redforged.llmdash;

import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
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
 * WebViewClient intercepts it, keeps every navigation of the round-trip
 * inside the WebView, then reads the JWT from the server callback page's
 * localStorage and stores it under the same SharedPreferences group
 * (@capacitor/preferences default: "CapacitorStorage") that the app's
 * storage.ts reads, before returning to the bundled app UI.
 */
public class MainActivity extends BridgeActivity {

    private static final String OAUTH_SCHEME = "llmdash-oauth";
    private static final String TOKEN_PREFS = "CapacitorStorage";
    private static final String TOKEN_KEY = "llmdash_token";
    private static final long OAUTH_TIMEOUT_MS = 10 * 60 * 1000L;

    /**
     * Extrovert's consent page renders its hidden _csrf field empty on a fresh
     * session (the server generates the CSRF token lazily — only when some page
     * of the session renders a CSRF form — and the session is regenerated at
     * login), so clicking "Authorize" POSTs _csrf= and the provider answers
     * "CSRF token missing or invalid. Re-open the authorization request.".
     * This fetches a same-origin page that renders a CSRF form (the homepage),
     * which makes the server mint the token for this session, then fills the
     * consent form with it before submit.
     */
    private static final String CONSENT_CSRF_JS =
            "(function(){"
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
            + "if(!input||input.value)return;"
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
            + "fill();"
            + "form.addEventListener('submit',function(e){"
            + "if(!input.value){e.preventDefault();fill().then(function(){form.submit();});}"
            + "});"
            + "})();";

    private LlmdashWebViewClient webViewClient;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        webViewClient = new LlmdashWebViewClient();
        getBridge().setWebViewClient(webViewClient);
    }

    private final class LlmdashWebViewClient extends BridgeWebViewClient {

        private boolean oauthMode = false;
        private String oauthServerHost = null;
        private String oauthProviderHost = null;
        private long oauthStartedAt = 0L;

        LlmdashWebViewClient() {
            super(getBridge());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            Uri url = request.getUrl();
            if (OAUTH_SCHEME.equals(url.getScheme())) {
                startOAuthFlow(view, url);
                return true;
            }
            if (oauthMode && isExpired()) {
                endOAuthFlow(view);
                return true;
            }
            if (oauthMode && isHttp(url)) {
                // Keep the whole OIDC round-trip inside the WebView so the
                // callback page's localStorage (where the JWT lands) stays
                // readable from native code.
                return false;
            }
            return super.shouldOverrideUrlLoading(view, request);
        }

        @Override
        @SuppressWarnings("deprecation")
        public boolean shouldOverrideUrlLoading(WebView view, String urlString) {
            Uri url = Uri.parse(urlString);
            if (OAUTH_SCHEME.equals(url.getScheme())) {
                startOAuthFlow(view, url);
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

        private void startOAuthFlow(WebView view, Uri url) {
            // llmdash-oauth://start?target=<extrovert authorize url>&server=<server origin>
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
            if (target == null || !isHttp(Uri.parse(target))) {
                return;
            }
            oauthProviderHost = Uri.parse(target).getHost();

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
            String server = url.getQueryParameter("server");
            oauthServerHost = server != null ? Uri.parse(server).getHost() : null;
            if (server != null && isHttp(Uri.parse(server))) {
                clearCookiesFor(server);
            }

            oauthMode = true;
            oauthStartedAt = System.currentTimeMillis();
            final String finalTarget = target;
            view.post(() -> view.loadUrl(finalTarget));
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap favicon) {
            super.onPageStarted(view, url, favicon);
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

        @Override
        public void onPageFinished(WebView view, String url) {
            super.onPageFinished(view, url);
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
            // it, same origin) — read it natively and hand it to the app.
            final String pageUrl = url;
            view.evaluateJavascript(
                    "(function(){ try { var t = localStorage.getItem('" + TOKEN_KEY + "'); return t ? t : null; } catch(e) { return null; } })()",
                    (ValueCallback<String>) value -> {
                        String token = unquoteJson(value);
                        if (token != null && !token.isEmpty()) {
                            storeToken(token);
                            endOAuthFlow(view);
                        } else if (pageUrl.contains("/api/auth/extrovert/callback")) {
                            // Error / expired / cancelled login — drop back into the app.
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
