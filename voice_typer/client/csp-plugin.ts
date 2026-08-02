/**
 * Per-page CSP emitted at build time.
 *
 * Both index.html and bubble.html ship a CSP meta tag. The strict policy
 * is also applied via the HTTP-header route in main/index.ts
 * (onHeadersReceived); this Vite plugin rewrites the meta tag in
 * `transformIndexHtml` so it matches the current mode:
 *
 * - Dev (command === 'serve'): emit CSP_DEV with `unsafe-eval` and
 *   `unsafe-inline` for script-src (Vite HMR + React Refresh preamble +
 *   eval-based sourcemaps need them) and ws://localhost:* / http://localhost:*
 *   in connect-src for the HMR websocket.
 * - Prod (command === 'build' && mode === 'production'): emit a strict
 *   `'self'`-only script-src. The production bundle has no inline scripts
 *   and no eval, so the strict policy is sufficient.
 *
 * C-DATA-1 (offline guarantee): `connect-src` is `'self'` ONLY in both
 * dev and prod. The previous `https://api.github.com` grant (originally
 * added so the Settings page's "Check for Updates" button could fetch
 * the GitHub releases API) was a C-DATA-1 violation — even an explicit
 * user click is a network call in the production code path, which the
 * offline guarantee forbids. The "Check for Updates" feature was
 * removed from `PrewarmAndUpdates.tsx` and replaced with a static
 * message directing users to open the GitHub releases page in their
 * browser. No renderer code path may issue any network request.
 *
 * Belt-and-suspenders: the onHeadersReceived HTTP-header CSP in
 * `main/bootstrap.ts::setupCsp()` still overrides the meta tag in Electron
 * (HTTP headers take precedence over meta tags per the CSP spec). The
 * plugin's job is to ensure the meta tag is also strict in production, so
 * the meta tag is no longer a backdoor.
 *
 * Fail-safe: the source HTML files (index.html / bubble.html) ship with
 * `CSP_PROD_MAIN` / `CSP_PROD_BUBBLE` (respectively) as their default meta
 * tag. If this plugin fails to fire in dev, HMR breaks visibly (loud
 * failure). If this plugin fails to fire in prod, the meta tag is already
 * strict (safe failure).
 */
import type { Plugin } from "vite";

/**
 * Production `connect-src` directive. `'self'` only — no external
 * origins. C-DATA-1 (offline guarantee): no network call may leave
 * the renderer in any code path.
 */
const CONNECT_SRC = "connect-src 'self'";

/**
 * Production CSP for the MAIN window (index.html). Strict `'self'`-only
 * `connect-src` — C-DATA-1 forbids any network call from the renderer,
 * including the previous "Check for Updates" fetch to api.github.com
 * (the feature was removed; see `PrewarmAndUpdates.tsx`).
 *
 * `object-src 'none'` is included to block <object>, <embed>,
 * and <applet> elements entirely. The renderer has no legitimate use
 * for these elements (all media is via <audio>/<video> with `media-src`
 * gating), so blocking them is a strict hardening with no functional
 * loss. Defense-in-depth against a future compromised renderer trying
 * to load a Flash/Java/PDF plugin as an exfiltration channel.
 */
export const CSP_PROD_MAIN = [
	"default-src 'self'",
	"script-src 'self'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	CONNECT_SRC,
	"object-src 'none'",
	"frame-ancestors 'none'",
	"form-action 'none'",
	"base-uri 'self'",
].join("; ");

/**
 * Production CSP for the BUBBLE window (bubble.html). Identical
 * `connect-src 'self'` policy — the bubble has no update-check surface
 * (and the main window's update-check surface has been removed too, so
 * both windows now share the same strict offline-only policy). A
 * compromised bubble renderer must not be able to phone home or
 * exfiltrate data via a CSP-permitted `connect-src`.
 *
 * `object-src 'none'` mirrors CSP_PROD_MAIN — the bubble has no
 * legitimate use for <object>/<embed>/<applet> elements either.
 */
export const CSP_PROD_BUBBLE = [
	"default-src 'self'",
	"script-src 'self'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	CONNECT_SRC,
	"object-src 'none'",
	"frame-ancestors 'none'",
	"form-action 'none'",
	"base-uri 'self'",
].join("; ");

/**
 * Back-compat alias for the old single-policy name. Main-window tests
 * and external consumers that import `CSP_PROD` continue to get the
 * main-window policy (the bubble now ships its own). New code should
 * import `CSP_PROD_MAIN` or `CSP_PROD_BUBBLE` explicitly.
 */
export const CSP_PROD = CSP_PROD_MAIN;

/**
 * Dev CSP. Allows `unsafe-eval` and `unsafe-inline` for script-src (Vite HMR
 * + React Refresh + eval sourcemaps). Adds ws://localhost:* and
 * http://localhost:* to connect-src for the HMR websocket + dev server
 * fetches. `connect-src` is otherwise `'self'` only — C-DATA-1 forbids
 * api.github.com (the previous "Check for Updates" fetch was removed
 * from the renderer; dev mode no longer needs the grant either).
 */
export const CSP_DEV = [
	"default-src 'self'",
	"script-src 'self' 'unsafe-eval' 'unsafe-inline'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	"connect-src 'self' ws://localhost:* http://localhost:*",
	"frame-ancestors 'none'",
	"form-action 'none'",
	"base-uri 'self'",
].join("; ");

/**
 * Build the CSP meta tag HTML for a given policy string.
 */
export function cspMetaTag(csp: string): string {
	return `<meta http-equiv="Content-Security-Policy" content="${csp}" />`;
}

/**
 * Pick the production CSP for a given HTML file path.
 *
 * Exported for unit tests so we can assert that bubble.html maps to
 * `CSP_PROD_BUBBLE` and index.html maps to `CSP_PROD_MAIN`. Both
 * policies now share the same strict `'self'`-only `connect-src`
 * (C-DATA-1); the function still routes by basename so future
 * per-window divergence remains possible without touching call sites.
 */
export function pickProdCsp(filePath: string): string {
	// Match on the basename so this works regardless of the absolute
	// path the Vite dev server / build pipeline hands us. The bubble
	// preload + renderer always load `bubble.html`; the main window
	// loads `index.html`.
	const base = filePath.split(/[\\/]/).pop() ?? "";
	return base === "bubble.html" ? CSP_PROD_BUBBLE : CSP_PROD_MAIN;
}

/**
 * Vite plugin that rewrites the CSP meta tag in index.html / bubble.html
 * based on the current mode (dev vs prod) and which file is being
 * transformed.
 */
export function cspEmissionPlugin(): Plugin {
	let isProduction = false;
	return {
		name: "voice-typer:csp-emission",
		// Run in both serve and build so dev gets the permissive CSP and prod
		// gets the strict CSP.
		apply: () => true,
		configResolved(config) {
			// electron-vite runs the renderer build with command='build' and
			// mode='production' for `electron-vite build`, and command='serve'
			// and mode='development' for `electron-vite dev`.
			isProduction = config.command === "build" && config.mode === "production";
		},
		transformIndexHtml: {
			// Run before Vite injects its HMR client + React Refresh preamble
			// so we replace the CSP meta tag before any inline scripts are
			// added (those need 'unsafe-inline' to execute, which the dev CSP
			// provides).
			order: "pre",
			handler(html: string, ctx?: { path?: string; filename?: string }) {
				// Pick the per-window prod policy. In dev we always use
				// CSP_DEV (the dev server needs the HMR websocket regardless
				// of which window is loading).
				const filePath = ctx?.path ?? ctx?.filename ?? "";
				const csp = isProduction ? pickProdCsp(filePath) : CSP_DEV;
				const metaTag = cspMetaTag(csp);
				// Replace any existing CSP meta tag (single- or double-quote
				// attribute form, with or without self-closing slash). If no
				// CSP meta tag is present, inject one before </head>.
				const cspRegex =
					/<meta\s+http-equiv=["']Content-Security-Policy["'][^>]*?\/?>/i;
				if (cspRegex.test(html)) {
					return html.replace(cspRegex, metaTag);
				}
				return html.replace("</head>", `  ${metaTag}\n</head>`);
			},
		},
	};
}
