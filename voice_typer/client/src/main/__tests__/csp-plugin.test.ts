// @vitest-environment node
/**
 * CR-11 / R6-F5 unit tests for `csp-plugin.ts`.
 *
 * Verifies that the production CSP is split per window so the bubble
 * window's policy does NOT grant `connect-src https://api.github.com`,
 * while the main window's policy still does (the explicit "Check for
 * Updates" button needs it after CR-11 removed the auto-mount fetch).
 */
import { describe, expect, it } from "vitest";
import {
	CSP_DEV,
	CSP_PROD,
	CSP_PROD_BUBBLE,
	CSP_PROD_MAIN,
	cspEmissionPlugin,
	cspMetaTag,
	pickProdCsp,
} from "../../../csp-plugin";

describe("CR-11 / R6-F5: per-window CSP split", () => {
	describe("CSP_PROD_MAIN", () => {
		it("includes https://api.github.com in connect-src", () => {
			expect(CSP_PROD_MAIN).toContain(
				"connect-src 'self' https://api.github.com",
			);
		});

		it("does NOT allow unsafe-eval or unsafe-inline in script-src (production hardening)", () => {
			// Match `script-src 'self'` exactly (no unsafe-* additions).
			// `style-src 'self' 'unsafe-inline'` IS allowed — Tailwind 4
			// needs it. So we assert the script-src directive is strict,
			// not the entire CSP string.
			expect(CSP_PROD_MAIN).toMatch(/script-src 'self'(?:;|$)/);
		});

		it("denies frame-ancestors and form-action", () => {
			expect(CSP_PROD_MAIN).toContain("frame-ancestors 'none'");
			expect(CSP_PROD_MAIN).toContain("form-action 'none'");
		});

		it("allows 'unsafe-inline' in style-src (Tailwind 4 runtime injection)", () => {
			expect(CSP_PROD_MAIN).toMatch(/style-src 'self' 'unsafe-inline'/);
		});
	});

	describe("CSP_PROD_BUBBLE", () => {
		it("does NOT include https://api.github.com in connect-src", () => {
			expect(CSP_PROD_BUBBLE).not.toContain("api.github.com");
			expect(CSP_PROD_BUBBLE).toContain("connect-src 'self'");
		});

		it("does NOT allow unsafe-eval or unsafe-inline in script-src", () => {
			expect(CSP_PROD_BUBBLE).toMatch(/script-src 'self'(?:;|$)/);
		});

		it("denies frame-ancestors and form-action", () => {
			expect(CSP_PROD_BUBBLE).toContain("frame-ancestors 'none'");
			expect(CSP_PROD_BUBBLE).toContain("form-action 'none'");
		});

		it("is strictly narrower than CSP_PROD_MAIN (no extra grants)", () => {
			// The bubble policy must be a subset of the main policy EXCEPT
			// for the removed api.github.com. Concretely: every directive
			// in CSP_PROD_BUBBLE must appear verbatim in CSP_PROD_MAIN.
			const bubbleDirectives = CSP_PROD_BUBBLE.split("; ").map((s) => s.trim());
			for (const d of bubbleDirectives) {
				expect(CSP_PROD_MAIN).toContain(d);
			}
		});
	});

	describe("CSP_PROD (back-compat alias)", () => {
		it("equals CSP_PROD_MAIN (the main-window policy)", () => {
			expect(CSP_PROD).toBe(CSP_PROD_MAIN);
		});
	});

	describe("CSP_DEV", () => {
		it("allows unsafe-eval and unsafe-inline in script-src (for Vite HMR)", () => {
			expect(CSP_DEV).toContain("'unsafe-eval'");
			expect(CSP_DEV).toContain("'unsafe-inline'");
		});

		it("includes ws://localhost:* and http://localhost:* in connect-src (HMR websocket)", () => {
			expect(CSP_DEV).toContain("ws://localhost:*");
			expect(CSP_DEV).toContain("http://localhost:*");
		});
	});

	describe("pickProdCsp(file)", () => {
		it("returns CSP_PROD_BUBBLE for bubble.html", () => {
			expect(pickProdCsp("/abs/path/to/bubble.html")).toBe(CSP_PROD_BUBBLE);
		});

		it("returns CSP_PROD_MAIN for index.html", () => {
			expect(pickProdCsp("/abs/path/to/index.html")).toBe(CSP_PROD_MAIN);
		});

		it("returns CSP_PROD_MAIN as the default for unknown files", () => {
			expect(pickProdCsp("/abs/path/to/unknown.html")).toBe(CSP_PROD_MAIN);
			expect(pickProdCsp("")).toBe(CSP_PROD_MAIN);
		});

		it("handles Windows-style backslash paths", () => {
			expect(pickProdCsp("C:\\Users\\app\\bubble.html")).toBe(CSP_PROD_BUBBLE);
		});
	});

	describe("cspMetaTag(csp)", () => {
		it("wraps the policy in a <meta> tag with the http-equiv attribute", () => {
			const tag = cspMetaTag("default-src 'self'");
			expect(tag).toContain('http-equiv="Content-Security-Policy"');
			expect(tag).toContain("content=\"default-src 'self'\"");
		});
	});

	describe("cspEmissionPlugin()", () => {
		it("returns a Vite Plugin with transformIndexHtml", () => {
			const plugin = cspEmissionPlugin();
			expect(plugin.name).toBe("voice-typer:csp-emission");
			expect(plugin.transformIndexHtml).toBeDefined();
		});

		it("applies => true so it runs in both serve and build", () => {
			const plugin = cspEmissionPlugin();
			// `apply` is typed as a union of string | function in Vite's
			// Plugin type. Cast to a callable so we can exercise the
			// function branch (the plugin always returns `true`).
			const applyFn = plugin.apply as
				| ((this: void, config: unknown, env: unknown) => boolean)
				| undefined;
			expect(applyFn?.({ command: "serve" }, {})).toBe(true);
			expect(applyFn?.({ command: "build" }, {})).toBe(true);
		});
	});
});
