/**
 * Per-page CSP emitted at build time.
 *
 * Both index.html and bubble.html ship a CSP meta tag. The strict policy
 * is also applied via the HTTP-header route in main/index.ts
 * (onHeadersReceived); this Vite plugin rewrites the meta tag in
 * `transformIndexHtml` so it matches the current mode:
 *
 * `frame-ancestors` is deliberately EXCLUDED from the meta CSP (and from
 * every policy below): the CSP spec only honors `frame-ancestors` when
 * delivered as an HTTP header — a `<meta>` occurrence is ignored AND
 * triggers a console warning. The Electron main process enforces it via
 * the HTTP-header CSP in `bootstrap.ts::_buildCsp` (onHeadersReceived),
 * which keeps the frame protection while the meta tag stays warning-free.
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
 *
 * NOTE: `connect-src 'self'` is inlined as a string literal (rather than
 * referenced via a shared `CONNECT_SRC` const) so static extractors —
 * including the pytest harness in `tests/test_csp_emission.py` —
 * can recover the full CSP string by reading the array's string-literal
 * elements. The previous `CONNECT_SRC` indirection caused the extracted
 * `CSP_PROD` to silently drop `connect-src 'self'`, which then failed
 * to match the built HTML's CSP (the JS runtime evaluates the const
 * substitution; the static extractor cannot). Inlining is lossless
 * because the value is the same in both windows.
 */
export const CSP_PROD_MAIN = [
	"default-src 'self'",
	"script-src 'self'",
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"media-src 'self' data:",
	"connect-src 'self'",
	"object-src 'none'",
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
	"connect-src 'self'",
	"object-src 'none'",
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
 * Virtual-module IDs for the externalized locale-bootstrap script.
 *
 * WHY: index.html and bubble.html each ship an inline `<script>` that
 * reads `localStorage["voice-typer-ui-locale"]` and sets
 * `document.documentElement.lang` (and, for bubble.html, `.dir`) BEFORE
 * React mounts — so screen readers announce first-paint content in the
 * correct language. The strict production CSP (`script-src 'self'`)
 * forbids inline scripts, so the inline block would either need
 * 'unsafe-inline' (a downgrade we refuse — Hard Rule 4) or a per-block
 * SHA-256 hash in the CSP (which would make the built CSP diverge from
 * `CSP_PROD`, breaking the equality assertion in `test_built_*_html_csp_matches_csp_prod`).
 *
 * The clean fix is to externalize the inline script at build time:
 * `transformIndexHtml` (prod only) replaces the inline `<script>` with
 * `<script type="module" src="virtual:...">`, stashes the original code
 * in `extractedLocaleCode`, and Vite's `resolveId` + `load` hooks serve
 * the stashed code as a virtual module. The built HTML ends up with a
 * hashed `<script type="module" src="./assets/locale-bootstrap-*.js">`
 * reference and zero inline scripts — the strict CSP is satisfied
 * without any downgrade.
 *
 * Two virtual IDs (one per window) are used because bubble.html's
 * bootstrap additionally sets `document.documentElement.dir` for RTL
 * locales. The HTML is the single source of truth — the plugin extracts
 * the verbatim inline-script body and serves it back via `load`, so a
 * future edit to the inline script (e.g. adding a 9th locale) is picked
 * up automatically with no plugin change.
 *
 * In dev, the inline script is left in place — `CSP_DEV` allows
 * `'unsafe-inline'` for script-src so it runs as-is.
 */
const LOCALE_VIRTUAL_ID_MAIN = "virtual:voice-typer-locale-bootstrap-main";
const LOCALE_VIRTUAL_ID_BUBBLE = "virtual:voice-typer-locale-bootstrap-bubble";
const LOCALE_RESOLVED_MAIN = `\0${LOCALE_VIRTUAL_ID_MAIN}`;
const LOCALE_RESOLVED_BUBBLE = `\0${LOCALE_VIRTUAL_ID_BUBBLE}`;

/**
 * Match the inline locale-detection `<script>` block in index.html /
 * bubble.html. The block is identified by the `voice-typer-ui-locale`
 * localStorage key (which is unique to this script in the HTML) inside
 * a bare `<script>` tag (no `src`, no `type` — so we don't accidentally
 * match the `<script type="module">` blocks that Vite processes
 * separately). Non-greedy capture so we stop at the first `</script>`.
 */
const INLINE_LOCALE_SCRIPT_RE =
	/<script>([\s\S]*?voice-typer-ui-locale[\s\S]*?)<\/script>/;

/**
 * Vite plugin that rewrites the CSP meta tag in index.html / bubble.html
 * based on the current mode (dev vs prod) and which file is being
 * transformed. In production, it also externalizes the inline
 * locale-detection `<script>` block as a virtual module so the strict
 * CSP (`script-src 'self'`) is satisfied without 'unsafe-inline'.
 */
export function cspEmissionPlugin(): Plugin {
	let isProduction = false;
	// Stash the verbatim inline-script body keyed by resolved virtual
	// ID. Populated by `transformIndexHtml` (prod only) and read by
	// `load`. Module-scoped to the plugin instance so concurrent
	// builds of index.html + bubble.html don't clobber each other.
	const extractedLocaleCode = new Map<string, string>();
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
		resolveId(id) {
			// Intercept the virtual locale-bootstrap module specifiers
			// emitted into the HTML by `transformIndexHtml`. Returning
			// a `\0`-prefixed ID tells Vite this is a virtual module
			// (not a file on disk) — Vite then calls our `load` hook
			// for the source.
			if (id === LOCALE_VIRTUAL_ID_MAIN) return LOCALE_RESOLVED_MAIN;
			if (id === LOCALE_VIRTUAL_ID_BUBBLE) return LOCALE_RESOLVED_BUBBLE;
			return null;
		},
		load(id) {
			// Serve the stashed inline-script body as the virtual
			// module's source. The body is the original IIFE from the
			// HTML — a valid ESM module (an expression statement that
			// runs once on first import). Vite bundles it as a hashed
			// asset and rewrites the `<script src="...">` URL to point
			// at the hashed file.
			const code = extractedLocaleCode.get(id);
			if (code !== undefined) return code;
			return null;
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
					html = html.replace(cspRegex, metaTag);
				} else {
					html = html.replace("</head>", `  ${metaTag}\n</head>`);
				}

				// In production, externalize the inline locale-detection
				// `<script>` block as a virtual module so the strict CSP
				// (`script-src 'self'`) is satisfied without 'unsafe-inline'.
				// In dev, leave the inline script in place — CSP_DEV allows
				// 'unsafe-inline' for script-src (Vite HMR + React Refresh
				// preamble already need it).
				if (isProduction) {
					const isBubble =
						(filePath.split(/[\\/]/).pop() ?? "") === "bubble.html";
					const virtualId = isBubble
						? LOCALE_VIRTUAL_ID_BUBBLE
						: LOCALE_VIRTUAL_ID_MAIN;
					const resolvedId = isBubble
						? LOCALE_RESOLVED_BUBBLE
						: LOCALE_RESOLVED_MAIN;
					const match = html.match(INLINE_LOCALE_SCRIPT_RE);
					if (match) {
						// Stash the verbatim inline-script body so `load`
						// can return it when Vite resolves the virtual
						// module. The body is the IIFE between `<script>`
						// and `</script>` (capture group 1).
						extractedLocaleCode.set(resolvedId, match[1] ?? "");
						html = html.replace(
							match[0] ?? "",
							`<script type="module" src="${virtualId}"></script>`,
						);
					}
				}

				return html;
			},
		},
	};
}
