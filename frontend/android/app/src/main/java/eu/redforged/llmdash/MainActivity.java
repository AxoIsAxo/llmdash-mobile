package eu.redforged.llmdash;

import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Bundle;
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
            String target = url.getQueryParameter("target");
            if (target != null && isHttp(Uri.parse(target))) {
                oauthMode = true;
                oauthStartedAt = System.currentTimeMillis();
                String server = url.getQueryParameter("server");
                oauthServerHost = server != null ? Uri.parse(server).getHost() : null;
                final String finalTarget = target;
                view.post(() -> view.loadUrl(finalTarget));
            }
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

            if (oauthServerHost == null || !oauthServerHost.equals(safeHost(url))) {
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
            oauthStartedAt = 0L;
            view.post(() -> view.loadUrl(homeUrl()));
        }

        private boolean isHttp(Uri url) {
            String s = url.getScheme();
            return "http".equals(s) || "https".equals(s);
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
