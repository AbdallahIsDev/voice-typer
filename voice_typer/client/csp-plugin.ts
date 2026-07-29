/**
 * RW-3: per-page CSP emitted at build time.
 *
 * Prior to RW-3, both index.html and bubble.html shipped a CSP meta tag with
 * `script-src 'self' 'unsafe-eval' 'unsafe-inline'` baked in — even in the
 * production build. The strict policy was applied only via the HTTP-header
 * route in main/index.ts (onHeadersReceived), which left the meta tag itself
 * loose. If the HTTP-header route ever failed to fire (e.g. an Electron
 * upgrade changed file:// header handling, or someone opened the HTML file
 * outside Electron), the loose meta tag would silently take over.
 *
 * This Vite plugin rewrites the CSP meta tag in `transformIndexHtml` so it
 * matches the current mode:
 *
 * - Dev (command === 'serve'): emit CSP_DEV with `unsafe-eval` and
 *   `unsafe-inline` for script-src (Vite HMR + React Refresh preamble +
 *   eval-based sourcemaps need them) and ws://localhost:* / http://localhost:*
 *   in connect-src for the HMR websocket.
 * - Prod (command === 'build' && mode === 'production'): emit a strict
 *   `'self'`-only script-src. The production bundle has no inline scripts
 *   and no eval, so the strict policy is sufficient.
 *
 * CR-11 / R6-F5 (fix): the CSP `connect-src` is now split per window so the
 * bubble.html policy does NOT include `https://api.github.com`. The main
 * window's policy keeps it (the Settings page's "Check for Updates" button
 * still fetches the GitHub releases API on an EXPLICIT user click — see
 * CR-11 fix in `PrewarmAndUpdates.tsx` which removed the auto-mount
 * `useEffect`). The bubble has NO update-check surface, so granting it
 * `connect-src https://api.github.com` would be dead attack surface.
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
 * Build the production `connect-src` directive for a given window.
 *
 * `api.github.com` is included ONLY for the main window — the bubble has
 * no update-check surface (CR-11 / R6-F5).
 */
function buildConnectSrc(opts: { allowGitHub: boolean }): string {
	const parts = ["'self'"];
	if (opts.allowGitHub) parts.push("https://api.github.com");
	return `connect-src ${parts.join(" ")}`;
}

/**
 * Production CSP for the MAIN window (index.html). Includes
 * `https://api.github.com` in `connect-src` so the Settings page's
 * explicit "Check for Updates" button can fetch the GitHub releases
 * API. CR-11 removed the auto-mount `useEffect` that previously
 * fired the check on every Settings open, so this URL is now reached
 * ONLY on a deliberate user click.
 *
 * FR-103: `object-src 'none'` is included to block <object>, <embed>,
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
	buildConnectSrc({ allowGitHub: true }),
	"object-src 'none'",
	"frame-ancestors 'none'",
	"form-action 'none'",
	"base-uri 'self'",
].join("; ");

/**
 * Production CSP for the BUBBLE window (bubble.html). Does NOT include
 * `https://api.github.com` in `connect-src` — the bubble has no
 * update-check surface (CR-11 / R6-F5). A compromised bubble renderer
 * must not be able to phone home to GitHub or exfiltrate data via a
 * CSP-permitted `connect-src`.
 *
 * FR-103: `object-src 'none'` mirrors CSP_PROD_MAIN — the bubble has
 * no legitimate use for <object>/<embed>/<applet> elements either.
 */
export const CSP_PROD_BUBBLE = [
	"default-src 'self'",
	"script-src 'self'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	buildConnectSrc({ allowGitHub: false }),
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
 * fetches. `api.github.com` is retained in dev so the explicit "Check for
 * Updates" button works against the live API during development.
 */
export const CSP_DEV = [
	"default-src 'self'",
	"script-src 'self' 'unsafe-eval' 'unsafe-inline'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	"connect-src 'self' https://api.github.com ws://localhost:* http://localhost:*",
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
 * Exported for unit tests (R6-F5) so we can assert that bubble.html
 * maps to `CSP_PROD_BUBBLE` (no `api.github.com`) and index.html
 * maps to `CSP_PROD_MAIN` (with `api.github.com`).
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
 * transformed (CR-11 / R6-F5 per-window split).
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
				// CR-11 / R6-F5: pick the per-window prod policy. In dev we
				// always use CSP_DEV (the dev server needs the HMR websocket
				// regardless of which window is loading).
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
