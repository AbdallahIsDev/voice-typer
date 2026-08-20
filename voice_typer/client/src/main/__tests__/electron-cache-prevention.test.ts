// @vitest-environment node
/**
 * R6: disk-cache prevention in the Electron main entry point.
 *
 * The renderer only ever loads the local bundle (`file://` via
 * `loadFile` in production, `http://localhost:5173` in dev) — it never
 * fetches remote content — yet Chromium's disk caches still accumulated
 * ~400 MB of stale entries in `electron-profile/` (212 MB HTTP `Cache` +
 * 180 MB V8 `Code Cache` from dev-server URLs). `index.ts` appends two
 * documented Chromium content-layer switches at module load time:
 *
 *   - `disable-http-cache` — disables the DISK cache for HTTP requests
 *     (the in-memory cache stays, so HMR / repeated loads are unaffected).
 *   - `v8-cache-options=none` — disables V8's on-disk script code cache
 *     (`Code Cache/`); production loads via `file://` where code cache is
 *     not used anyway (http(s) URLs only), so nothing is lost.
 *
 * Both must be appended BEFORE `app.whenReady()` — Chromium parses the
 * switches at browser-process startup. These tests assert the switches
 * exist in `index.ts` and sit before the `whenReady` call (the same
 * source-assertion pattern as `electron-vite-sourcemap.test.ts`), so a
 * future refactor can't silently move them after `whenReady` and
 * re-introduce the disk cache.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const INDEX_SRC = readFileSync(resolve(__dirname, "../index.ts"), "utf-8");

describe("R6: Chromium disk-cache prevention in index.ts", () => {
	it("appends the disable-http-cache switch", () => {
		expect(INDEX_SRC).toMatch(
			/app\.commandLine\.appendSwitch\(\s*["']disable-http-cache["']\s*\)/,
		);
	});

	it("disables the V8 on-disk code cache", () => {
		expect(INDEX_SRC).toMatch(
			/app\.commandLine\.appendSwitch\(\s*["']v8-cache-options["']\s*,\s*["']none["']\s*\)/,
		);
	});

	it("applies the switches before app.whenReady()", () => {
		// Anchor on the ACTUAL call (`app.whenReady().then(`), not the
		// bare mention in index.ts's module docstring (line ~30).
		const whenReadyIdx = INDEX_SRC.indexOf("app.whenReady().then(");
		const disableHttpIdx = INDEX_SRC.indexOf("disable-http-cache");
		const v8CacheIdx = INDEX_SRC.indexOf("v8-cache-options");
		expect(whenReadyIdx).toBeGreaterThan(-1);
		expect(disableHttpIdx).toBeGreaterThan(-1);
		expect(v8CacheIdx).toBeGreaterThan(-1);
		expect(disableHttpIdx).toBeLessThan(whenReadyIdx);
		expect(v8CacheIdx).toBeLessThan(whenReadyIdx);
	});
});
