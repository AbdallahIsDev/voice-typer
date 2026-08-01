/**
 * @vitest-environment node
 *
 * C-DATA-1 unit tests for `bootstrap.ts::_buildCsp()`.
 *
 * The HTTP-header CSP injected by `setupCsp()` via
 * `session.defaultSession.webRequest.onHeadersReceived` is the
 * authoritative CSP for every renderer window — HTTP headers take
 * precedence over the per-window meta tags emitted by `csp-plugin.ts`
 * (CSP spec: when both sources are present, the intersection of allowed
 * sources is enforced, so the stricter HTTP-header policy wins).
 *
 * These tests pin the C-DATA-1 offline guarantee:
 *   - `connect-src` MUST be exactly `'self'` (no `api.github.com`).
 *   - All other directives (`default-src`, `script-src`, `style-src`,
 *     `img-src`, `font-src`, `media-src`, `frame-ancestors`,
 *     `form-action`, `base-uri`) MUST be preserved — only `connect-src`
 *     was tightened.
 *
 * The mocks mirror `bootstrap.test.ts` so importing `bootstrap.ts`
 * (which transitively imports `electron`, `./single_instance`,
 * `./python`, `./state`) does not pull in the real Electron runtime.
 */
import { describe, expect, it, vi } from "vitest";

// vi.mock calls are hoisted by vitest to before all imports — they
// intercept the imports done by `bootstrap.ts` even though the import
// statement appears later in the file.
vi.mock("electron", () => ({
	app: {
		getPath: () => "/tmp/vt-mock-userdata",
		isPackaged: true,
	},
	dialog: {
		showErrorBox: vi.fn(),
	},
	session: {
		defaultSession: {
			webRequest: {
				onHeadersReceived: vi.fn(),
			},
		},
	},
}));

vi.mock("../single_instance", () => ({
	computeConfigDir: () => "/tmp/vt-mock-userdata",
	clearElectronPidFile: vi.fn(),
}));

vi.mock("../python", () => ({
	stopPython: vi.fn(),
}));

vi.mock("../state", () => ({
	state: { sessionNonce: "" },
}));

// Import the unit under test AFTER the mocks are registered.
import { _buildCsp } from "../bootstrap";

describe("_buildCsp — C-DATA-1 offline guarantee", () => {
	describe("connect-src (the C-DATA-1 target)", () => {
		it("does NOT include api.github.com in production mode", () => {
			const csp = _buildCsp({ isPackaged: true });
			expect(csp).not.toContain("api.github.com");
		});

		it("does NOT include api.github.com in dev mode either", () => {
			// Dev mode adds 'unsafe-eval'/'unsafe-inline' to script-src, but
			// must NOT loosen connect-src — the offline guarantee holds in
			// both modes.
			const csp = _buildCsp({ isPackaged: false });
			expect(csp).not.toContain("api.github.com");
		});

		it("is exactly connect-src 'self' in production (no extra grants)", () => {
			const csp = _buildCsp({ isPackaged: true });
			// Extract the full connect-src directive and assert it is
			// exactly "connect-src 'self'" — no trailing hosts, no
			// 'unsafe-inline', nothing.
			const match = csp.match(/connect-src [^;]+/);
			expect(match).not.toBeNull();
			expect(match?.[0]).toBe("connect-src 'self'");
		});

		it("contains the connect-src 'self' substring for both modes", () => {
			expect(_buildCsp({ isPackaged: true })).toContain("connect-src 'self'");
			expect(_buildCsp({ isPackaged: false })).toContain("connect-src 'self'");
		});

		it("does not leak github.com under any spelling (https://, http://, //, bare host)", () => {
			// Defense-in-depth: a future refactor might switch the URL
			// scheme or drop the protocol. This catches all common
			// spellings.
			const variants = [
				"https://api.github.com",
				"http://api.github.com",
				"//api.github.com",
				"api.github.com",
				"*.github.com",
				"github.com",
			];
			for (const v of variants) {
				expect(_buildCsp({ isPackaged: true })).not.toContain(v);
				expect(_buildCsp({ isPackaged: false })).not.toContain(v);
			}
		});
	});

	describe("other directives preserved (only connect-src was tightened)", () => {
		const csp = _buildCsp({ isPackaged: true });

		it("preserves default-src 'self'", () => {
			expect(csp).toContain("default-src 'self'");
		});

		it("preserves strict script-src 'self' in production (no unsafe-*)", () => {
			// Match `script-src 'self'` exactly (no unsafe-* additions).
			expect(csp).toMatch(/script-src 'self'(?:;|$)/);
		});

		it("preserves style-src 'self' 'unsafe-inline' (Tailwind 4 runtime injection)", () => {
			expect(csp).toMatch(/style-src 'self' 'unsafe-inline'/);
		});

		it("preserves img-src 'self' data:", () => {
			expect(csp).toContain("img-src 'self' data:");
		});

		it("preserves font-src 'self' data:", () => {
			expect(csp).toContain("font-src 'self' data:");
		});

		it("preserves media-src 'self' data:", () => {
			expect(csp).toContain("media-src 'self' data:");
		});

		it("preserves frame-ancestors 'none'", () => {
			expect(csp).toContain("frame-ancestors 'none'");
		});

		it("preserves form-action 'none'", () => {
			expect(csp).toContain("form-action 'none'");
		});

		it("preserves base-uri 'self'", () => {
			expect(csp).toContain("base-uri 'self'");
		});
	});

	describe("dev-mode script-src relaxation (Vite HMR)", () => {
		it("adds 'unsafe-eval' and 'unsafe-inline' to script-src when isPackaged === false", () => {
			const csp = _buildCsp({ isPackaged: false });
			expect(csp).toContain("'unsafe-eval'");
			expect(csp).toContain("'unsafe-inline'");
			// The 'unsafe-inline' must appear in script-src (not just
			// style-src, which always has it) — assert by checking the
			// script-src directive directly.
			const scriptSrc = csp.match(/script-src [^;]+/)?.[0] ?? "";
			expect(scriptSrc).toContain("'unsafe-eval'");
			expect(scriptSrc).toContain("'unsafe-inline'");
		});

		it("does NOT add 'unsafe-eval' / 'unsafe-inline' to script-src in production", () => {
			const csp = _buildCsp({ isPackaged: true });
			const scriptSrc = csp.match(/script-src [^;]+/)?.[0] ?? "";
			expect(scriptSrc).not.toContain("'unsafe-eval'");
			// 'unsafe-inline' is allowed in style-src but NOT in script-src
			// for production. The directive must be exactly `script-src 'self'`.
			expect(scriptSrc).toBe("script-src 'self'");
		});
	});

	describe("structural invariants", () => {
		it("produces a non-empty string joined by '; '", () => {
			const csp = _buildCsp({ isPackaged: true });
			expect(typeof csp).toBe("string");
			expect(csp.length).toBeGreaterThan(0);
			expect(csp).toContain("; ");
		});

		it("emits exactly one connect-src directive (no duplicates)", () => {
			const csp = _buildCsp({ isPackaged: true });
			const matches = csp.match(/connect-src /g);
			expect(matches?.length).toBe(1);
		});

		it("connect-src is not the last directive (followed by at least one more)", () => {
			// Sanity check: the directive list still has the trailing
			// frame-ancestors / form-action / base-uri entries after
			// the connect-src slot, so removing api.github.com didn't
			// accidentally truncate the array.
			const csp = _buildCsp({ isPackaged: true });
			const idx = csp.indexOf("connect-src");
			expect(idx).toBeGreaterThan(-1);
			const after = csp.slice(idx);
			expect(after).toContain("frame-ancestors");
			expect(after).toContain("form-action");
			expect(after).toContain("base-uri");
		});
	});
});
