/**
 * @vitest-environment node
 *
 * Guards for the inline i18n-locale bootstrap scripts and the CSP meta
 * tags shipped in ``index.html`` and ``bubble.html``.
 *
 * These HTML files are the FIRST thing the renderer loads (before the
 * React tree mounts), so two invariants must hold:
 *
 *   1. The inline bootstrap script (which reads localStorage for the
 *      saved UI locale and sets ``document.documentElement.lang`` /
 *      ``.dir`` before first paint) MUST use block-scoped ``let`` /
 *      ``const`` declarations — no ``var``. ``var`` would leak to the
 *      global ``window`` scope and pollute the renderer's global
 *      namespace before the React app has a chance to set up its own
 *      module boundaries.
 *
 *   2. The strict production CSP meta tag MUST NOT grant
 *      ``connect-src https://api.github.com``. C-DATA-1 forbids any
 *      network call in the production code path — Voice Typer is an
 *      OFFLINE application. The previous ``https://api.github.com``
 *      grant was a latent C-DATA-1 violation (the explicit "Check for
 *      Updates" button would still phone home to GitHub on a user
 *      click); it has been removed. Update checks must route through
 *      the Python sidecar instead of a renderer-direct HTTPS call.
 *
 * Belt-and-suspenders: the HTTP-header CSP in
 * ``src/main/bootstrap.ts::_buildCsp()`` already restricts
 * ``connect-src`` to ``'self'`` and takes precedence over the meta
 * tag at runtime (CSP spec: HTTP headers win). This test pins the
 * meta tag itself so it stays in lockstep with the HTTP-header policy
 * and is no longer a backdoor if the HTTP-header route ever fails to
 * fire (e.g. an Electron upgrade changes file:// header handling, or
 * the page is opened outside Electron).
 *
 * Platform: Linux sandbox / Windows host / macOS host (pure static
 * file read — no DOM, no jsdom). Validation:
 *   VALIDATE ON LINUX HOST: cd voice_typer/client && npx vitest run \
 *     src/renderer/src/i18n/__tests__/html-bootstrap-csp.test.ts
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const RENDERER_DIR = resolve(__dirname, "../../..");
const HTML_FILES = {
	"index.html": resolve(RENDERER_DIR, "index.html"),
	"bubble.html": resolve(RENDERER_DIR, "bubble.html"),
} as const;

function readHtml(name: keyof typeof HTML_FILES): string {
	return readFileSync(HTML_FILES[name], "utf8");
}

function extractCsp(html: string): string {
	// The CSP meta tag uses double-quoted content="..." attribute. The
	// policy value itself contains single quotes (e.g. 'self'), so we
	// match up to the closing double-quote (not the next single-quote).
	const match = html.match(
		/<meta\s+http-equiv=["']Content-Security-Policy["'][^>]*?content="([^"]+)"/i,
	);
	expect(match, "CSP meta tag must be present").not.toBeNull();
	if (match == null || match[1] == null) {
		throw new Error("CSP meta tag must be present");
	}
	return match[1];
}

function extractCspConnectSrc(html: string): string {
	const csp = extractCsp(html);
	const connectSrcMatch = csp.match(/connect-src\s+([^;]+)/i);
	expect(
		connectSrcMatch,
		"CSP must have a connect-src directive",
	).not.toBeNull();
	if (connectSrcMatch == null || connectSrcMatch[1] == null) {
		throw new Error("CSP must have a connect-src directive");
	}
	return connectSrcMatch[1].trim();
}

describe("HTML inline i18n bootstrap scripts", () => {
	for (const fileName of Object.keys(HTML_FILES) as Array<
		keyof typeof HTML_FILES
	>) {
		describe(fileName, () => {
			it("uses `let`/`const` (not `var`) in the inline bootstrap script", () => {
				const html = readHtml(fileName);
				// Extract the inline bootstrap <script> block (the first
				// non-module <script> in <head> — the one that sets
				// document.documentElement.lang from localStorage).
				const scriptMatch = html.match(
					/<script>\s*\(\(\)\s*=>\s*\{[\s\S]*?\}\)\(\);\s*<\/script>/,
				);
				expect(
					scriptMatch,
					"inline i18n bootstrap script must be present",
				).not.toBeNull();
				if (scriptMatch == null || scriptMatch[0] == null) {
					throw new Error("inline i18n bootstrap script must be present");
				}
				const script = scriptMatch[0];

				// The bootstrap must NOT use `var` — block-scoped `let` /
				// `const` only, to avoid leaking to the global window scope.
				expect(script).not.toMatch(/\bvar\s+/);

				// The bootstrap MUST declare `locale`, `SUPPORTED`, and `tag`
				// with `let` (they are conditionally assigned inside try{}).
				expect(script).toMatch(/\blet\s+locale,\s+SUPPORTED,\s+tag\s*;/);
			});

			it("does not include any task IDs in comments (C-STYLE-1)", () => {
				const html = readHtml(fileName);
				// The inline bootstrap script's comments must not reference
				// task IDs / ticket numbers / session prefixes.
				const scriptMatch = html.match(
					/<script>\s*\(\(\)\s*=>\s*\{[\s\S]*?\}\)\(\);\s*<\/script>/,
				);
				expect(scriptMatch).not.toBeNull();
				if (scriptMatch == null || scriptMatch[0] == null) {
					throw new Error("inline i18n bootstrap script must be present");
				}
				const script = scriptMatch[0];
				// C-STYLE-1: no task IDs in source code (including comments).
				// The known historical prefix that previously appeared here
				// was a previous task ID; we accept any uppercase-prefix-plus-digits
				// pattern as a regression guard.
				expect(script).not.toMatch(/\/\/\s*[A-Z]{1,4}-\d+\s*:/);
			});
		});
	}
});

describe("HTML CSP meta tags — C-DATA-1 offline compliance", () => {
	for (const fileName of Object.keys(HTML_FILES) as Array<
		keyof typeof HTML_FILES
	>) {
		describe(fileName, () => {
			it("connect-src does NOT grant https://api.github.com (C-DATA-1)", () => {
				const html = readHtml(fileName);
				const connectSrc = extractCspConnectSrc(html);
				// C-DATA-1: app is OFFLINE — no external network calls
				// are permitted in the production code path. The CSP
				// meta tag is the strict production policy, so it must
				// not allow api.github.com.
				expect(connectSrc).not.toContain("api.github.com");
				expect(connectSrc).toContain("'self'");
			});

			it("connect-src is exactly 'self' (no other external origins)", () => {
				const html = readHtml(fileName);
				const connectSrc = extractCspConnectSrc(html);
				// The only allowed source for connect-src in the HTML
				// meta tag is 'self'. Any other origin (https://, http://,
				// wss://, etc.) would be a latent C-DATA-1 violation.
				const sources = connectSrc.split(/\s+/);
				expect(sources).toEqual(["'self'"]);
			});

			it("preserves the other hardening directives (script-src, frame-ancestors, form-action)", () => {
				const html = readHtml(fileName);
				const csp = extractCsp(html);

				// These directives were not changed by the C-DATA-1 fix —
				// verify they remain in place so the CSP is still strict.
				expect(csp).toMatch(/script-src 'self'(?:;|$)/);
				expect(csp).toContain("frame-ancestors 'none'");
				expect(csp).toContain("form-action 'none'");
				expect(csp).toContain("base-uri 'self'");
				expect(csp).toContain("default-src 'self'");
			});
		});
	}
});
