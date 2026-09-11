/**
 * SEC-012 Content-Security-Policy headers (HTTP).
 *
 * Split out of `bootstrap.ts` (step 3 of the bootstrap sequence).
 */
import { app, session } from "electron";

/**
 * Build the Content-Security-Policy header string.
 *
 * Exported as `_buildCsp` (test seam) so `bootstrap-csp.test.ts` can pin
 * the C-DATA-1 offline guarantee without having to drive the full
 * `setupCsp()` → `session.defaultSession.webRequest.onHeadersReceived`
 * wiring. `setupCsp()` is a thin wrapper that calls `_buildCsp` and
 * installs the result as an HTTP header via Electron's webRequest API.
 *
 * @param opts.isPackaged - mirrors `app.isPackaged`. In dev mode
 *   (`isPackaged === false`) Vite's dev server injects inline scripts
 *   (React Refresh preamble + HMR client) and uses eval for sourcemaps,
 *   so 'unsafe-inline' + 'unsafe-eval' are added to `script-src`. In
 *   production the strict 'self'-only directive applies.
 * @returns the CSP string, directives joined by `"; "`.
 */
export function _buildCsp(opts: { isPackaged: boolean }): string {
	return [
		"default-src 'self'",
		`script-src 'self'${opts.isPackaged === false ? " 'unsafe-eval' 'unsafe-inline'" : ""}`,
		"style-src 'self' 'unsafe-inline'",
		"img-src 'self' data:",
		"font-src 'self' data:",
		"media-src 'self' data:",
		// C-DATA-1: connect-src restricted to 'self', no external network calls. Cloud-test/check-update calls must route through the Python sidecar.
		"connect-src 'self'",
		"frame-ancestors 'none'",
		"form-action 'none'",
		"base-uri 'self'",
	].join("; ");
}

/**
 * SEC-012 Content Security Policy (HTTP headers).
 *
 * CSP is also set via <meta> tags in index.html and bubble.html —
 * for packaged (file://) loads those meta policies are the only CSP
 * that applies, because `onHeadersReceived` never fires for file://
 * requests (there is no HTTP response to intercept). Certain
 * directives are additionally restricted to the HTTP-header channel
 * by the CSP spec, `frame-ancestors` is ignored when delivered via
 * <meta>, so the header CSP installed here is what actually enforces
 * `frame-ancestors 'none'`, and it does so in DEV MODE ONLY
 * (http://localhost:5173 responses flow through the webRequest API).
 *
 * In production the header CSP never fires (file:// loads), so
 * `frame-ancestors` is not spec-enforced on any channel. The residual
 * framing risk is negligible because of the compensating controls on
 * every window: the deny-all `setWindowOpenHandler`
 * (`windows/input-nav-guard.ts`, every renderer-initiated
 * `window.open()` is denied) and `sandbox: true` in the
 * `webPreferences` block (`windows/window-chrome.ts`), plus the meta
 * CSP's remaining directives (`form-action 'none'`, `base-uri`,
 * `default-src 'self'` …) which the spec DOES honor in <meta> form.
 *
 * In dev mode (app.isPackaged === false), Vite's dev server injects
 * inline scripts (React Refresh preamble + HMR client) and uses eval
 * for sourcemaps.  We add 'unsafe-inline' and 'unsafe-eval' only in
 * dev mode to allow these.  Production builds have no inline scripts
 * or eval, so the strict 'self' directive applies and inline event
 * handlers (onclick="...") remain blocked.
 */
export function setupCsp(): void {
	const CSP = _buildCsp({ isPackaged: app.isPackaged });

	session.defaultSession.webRequest.onHeadersReceived(
		(
			details: Electron.OnHeadersReceivedListenerDetails,
			callback: (headers: Electron.HeadersReceivedResponse) => void,
		) => {
			callback({
				responseHeaders: {
					...details.responseHeaders,
					"Content-Security-Policy": [CSP],
				},
			});
		},
	);
}
