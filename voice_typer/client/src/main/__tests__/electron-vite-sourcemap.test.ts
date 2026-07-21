// @vitest-environment node
/**
 * R6-F13 unit tests for `electron.vite.config.ts` and its CI-only
 * companion files `electron.vite.main.ts` + `electron.vite.renderer.ts`.
 *
 * Verifies the per-section `build.sourcemap` configuration:
 *   - main → `sourcemap: false`
 *   - preload → `sourcemap: false`
 *   - renderer → `sourcemap: command === "serve"` (true in dev, false in build)
 *
 * Source-map leakage in production .asar would expose main-process
 * source (IPC handlers, ALLOWED_COMMANDS, TCP plumbing) to anyone
 * who unzips the package.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const CONFIG_SRC = readFileSync(
	resolve(__dirname, "../../../electron.vite.config.ts"),
	"utf-8",
);

const MAIN_CI_SRC = readFileSync(
	resolve(__dirname, "../../../electron.vite.main.ts"),
	"utf-8",
);

const RENDERER_CI_SRC = readFileSync(
	resolve(__dirname, "../../../electron.vite.renderer.ts"),
	"utf-8",
);

describe("R6-F13: electron.vite.config.ts sourcemap config", () => {
	it("main section has `sourcemap: false`", () => {
		// Locate the `main:` block (top-level key) and assert sourcemap: false
		// appears before the next top-level key.
		const mainIdx = CONFIG_SRC.indexOf("main: {");
		expect(mainIdx).toBeGreaterThan(-1);
		const preloadIdx = CONFIG_SRC.indexOf("preload: {", mainIdx);
		const mainBlock = CONFIG_SRC.slice(mainIdx, preloadIdx);
		expect(mainBlock).toMatch(/sourcemap:\s*false/);
	});

	it("preload section has `sourcemap: false`", () => {
		const preloadIdx = CONFIG_SRC.indexOf("preload: {");
		expect(preloadIdx).toBeGreaterThan(-1);
		const rendererIdx = CONFIG_SRC.indexOf("renderer: {", preloadIdx);
		const preloadBlock = CONFIG_SRC.slice(preloadIdx, rendererIdx);
		expect(preloadBlock).toMatch(/sourcemap:\s*false/);
	});

	it('renderer section has `sourcemap: command === "serve"`', () => {
		const rendererIdx = CONFIG_SRC.indexOf("renderer: {");
		expect(rendererIdx).toBeGreaterThan(-1);
		const rendererBlock = CONFIG_SRC.slice(rendererIdx);
		expect(rendererBlock).toMatch(/sourcemap:\s*command\s*===\s*["']serve["']/);
	});

	it("config uses the defineConfig callback form (so `command` is in scope)", () => {
		// The renderer sourcemap depends on `command`, which is only
		// available if defineConfig is called with a function arg.
		expect(CONFIG_SRC).toMatch(
			/defineConfig\(\s*\(\s*\{\s*command\s*\}\s*\)\s*=>/,
		);
	});

	it("includes a comment explaining the R6-F13 security rationale", () => {
		expect(CONFIG_SRC).toContain("R6-F13");
		// The comment should mention security / source-leak rationale.
		expect(CONFIG_SRC.toLowerCase()).toMatch(/security|leak|expose/);
	});
});

describe("R6-F13: electron.vite.main.ts (CI-only) sourcemap config", () => {
	it("main section has `sourcemap: false`", () => {
		const mainIdx = MAIN_CI_SRC.indexOf("main: {");
		expect(mainIdx).toBeGreaterThan(-1);
		const preloadIdx = MAIN_CI_SRC.indexOf("preload: {", mainIdx);
		const mainBlock = MAIN_CI_SRC.slice(mainIdx, preloadIdx);
		expect(mainBlock).toMatch(/sourcemap:\s*false/);
	});

	it("preload section has `sourcemap: false`", () => {
		const preloadIdx = MAIN_CI_SRC.indexOf("preload: {");
		expect(preloadIdx).toBeGreaterThan(-1);
		const preloadBlock = MAIN_CI_SRC.slice(preloadIdx);
		expect(preloadBlock).toMatch(/sourcemap:\s*false/);
	});

	it("does NOT include a renderer section (CI-only main+preload config)", () => {
		// electron.vite.main.ts is the CI-only main+preload config — it
		// must NOT accidentally include a renderer block (the renderer
		// is built separately via electron.vite.renderer.ts).
		expect(MAIN_CI_SRC).not.toMatch(/^\s*renderer:\s*\{/m);
	});

	it("references R6-F13 in the security rationale comment", () => {
		expect(MAIN_CI_SRC).toContain("R6-F13");
	});
});

describe("R6-F13: electron.vite.renderer.ts (CI-only) sourcemap config", () => {
	it('renderer section has `sourcemap: command === "serve"`', () => {
		const rendererIdx = RENDERER_CI_SRC.indexOf("renderer: {");
		expect(rendererIdx).toBeGreaterThan(-1);
		const rendererBlock = RENDERER_CI_SRC.slice(rendererIdx);
		expect(rendererBlock).toMatch(/sourcemap:\s*command\s*===\s*["']serve["']/);
	});

	it("config uses the defineConfig callback form (so `command` is in scope)", () => {
		expect(RENDERER_CI_SRC).toMatch(
			/defineConfig\(\s*\(\s*\{\s*command\s*\}\s*\)\s*=>/,
		);
	});

	it("does NOT include main or preload sections (CI-only renderer config)", () => {
		expect(RENDERER_CI_SRC).not.toMatch(/^\s*main:\s*\{/m);
		expect(RENDERER_CI_SRC).not.toMatch(/^\s*preload:\s*\{/m);
	});

	it("references R6-F13 in the security rationale comment", () => {
		expect(RENDERER_CI_SRC).toContain("R6-F13");
	});
});
