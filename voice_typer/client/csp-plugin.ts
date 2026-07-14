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
 * - Prod (command === 'build' && mode === 'production'): emit CSP_PROD with
 *   only `'self'` for script-src. The production bundle has no inline scripts
 *   and no eval, so the strict policy is sufficient.
 *
 * Belt-and-suspenders: the onHeadersReceived HTTP-header CSP in main/index.ts
 * still overrides the meta tag in Electron (HTTP headers take precedence over
 * meta tags per the CSP spec). The plugin's job is to ensure the meta tag is
 * also strict in production, so the meta tag is no longer a backdoor.
 *
 * Fail-safe: the source HTML files (index.html / bubble.html) ship with
 * CSP_PROD as their default meta tag. If this plugin fails to fire in dev,
 * HMR breaks visibly (loud failure). If this plugin fails to fire in prod,
 * the meta tag is already strict (safe failure).
 */
import type { Plugin } from "vite";

/**
 * Production CSP. No `unsafe-eval`, no `unsafe-inline` for script-src.
 * Only `'self'` is allowed for scripts. The Vite production bundle has no
 * inline scripts and no eval, so this is sufficient.
 *
 * `style-src 'self' 'unsafe-inline'` is kept because Tailwind 4 injects
 * styles at runtime and the bubble.html source has an inline `<style>` block.
 * Style `'unsafe-inline'` is low-risk (no script execution from CSS in
 * modern browsers).
 */
export const CSP_PROD = [
	"default-src 'self'",
	"script-src 'self'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	"connect-src 'self' https://api.github.com",
	"frame-ancestors 'none'",
	"form-action 'none'",
	"base-uri 'self'",
].join("; ");

/**
 * Dev CSP. Allows `unsafe-eval` and `unsafe-inline` for script-src (Vite HMR
 * + React Refresh + eval sourcemaps). Adds ws://localhost:* and
 * http://localhost:* to connect-src for the HMR websocket + dev server
 * fetches.
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
 * Vite plugin that rewrites the CSP meta tag in index.html / bubble.html
 * based on the current mode (dev vs prod).
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
			handler(html: string) {
				const csp = isProduction ? CSP_PROD : CSP_DEV;
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
