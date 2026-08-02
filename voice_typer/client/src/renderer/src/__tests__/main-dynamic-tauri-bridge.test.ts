/**
 * regression test: ``main.tsx`` and ``bubble-main.tsx`` MUST use a
 * RUNTIME-GATED DYNAMIC ``import()`` for the Tauri bridge installer —
 * NOT a static top-level ``import "./lib/tauri-bridge/install"``.
 *
 * Background: the previous code had ``import "./lib/tauri-bridge/install"``
 * as a static top-level import in both entrypoints. Under Electron the
 * preload script (``src/preload/index.ts:19-117``) already installs
 * ``window.python`` / ``window.bubble`` / ``window.window_`` via
 * ``contextBridge.exposeInMainWorld``, so the ``install.ts`` module
 * (which pulls in the ~1.4 MB ``@tauri-apps/api`` graph) was shipped
 * but never executed under Electron — pure bundle bloat.
 *
 * The refactor replaced the static import with:
 *
 *   if (typeof window !== "undefined" &&
 *       (window as ...).__TAURI__?.core?.invoke) {
 *       await import("./lib/tauri-bridge/install");
 *   }
 *
 * Vite emits ``install.ts`` as a SEPARATE async chunk, fetched only
 * when ``window.__TAURI__?.core?.invoke`` is present (Tauri path).
 * Under Electron the gate is false and the chunk is never fetched.
 *
 * This test does a STATIC source analysis (reads the file as text and
 * regex-matches) rather than importing the entrypoint — importing
 * ``main.tsx`` would boot React inside a unit test, which is not what
 * we want here.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const MAIN_SRC = readFileSync(resolve(__dirname, "../main.tsx"), "utf-8");
const BUBBLE_SRC = readFileSync(
	resolve(__dirname, "../bubble-main.tsx"),
	"utf-8",
);

describe("tauri-bridge install is a runtime-gated dynamic import", () => {
	describe("main.tsx", () => {
		it('does NOT contain a static `import "./lib/tauri-bridge/install"`', () => {
			// A static side-effect import looks like:
			//   import "./lib/tauri-bridge/install";
			// (whitespace between `import` and the string literal, NOT
			// `import(`). This is the previous pattern that pulled 1.4 MB
			// into the Electron bundle. The `m` flag makes `^` / `$`
			// match line boundaries; the optional leading whitespace +
			// optional trailing semicolon catch indented / semicolon-less
			// variants.
			expect(MAIN_SRC).not.toMatch(
				/^\s*import\s+["']\.\/lib\/tauri-bridge\/install["'];?\s*$/m,
			);
		});

		it('contains a dynamic `import("./lib/tauri-bridge/install")`', () => {
			// The dynamic form is `import("./lib/tauri-bridge/install")`
			// (parenthesis immediately after `import`, no whitespace).
			// The optional `await ` prefix is not matched here — we only
			// assert the dynamic-import syntax is present.
			expect(MAIN_SRC).toMatch(
				/import\(\s*["']\.\/lib\/tauri-bridge\/install["']\s*\)/,
			);
		});

		it("gates the dynamic import on `window.__TAURI__?.core?.invoke`", () => {
			// The runtime gate is what makes the chunk "only fetched under
			// Tauri". Without the gate, Vite would still code-split the
			// chunk, but the entrypoint would unconditionally fetch it at
			// module load (defeating the purpose under Electron).
			expect(MAIN_SRC).toMatch(/__TAURI__/);
			expect(MAIN_SRC).toMatch(/core/);
			expect(MAIN_SRC).toMatch(/invoke/);
		});

		it('does NOT contain a static `import "./lib/tauri-bridge"` (bare, no /install)', () => {
			// The bare `./lib/tauri-bridge` side-effect import (without
			// `/install`) is the pre-split pattern — `index.ts` no longer
			// auto-invokes ``installTauriBridge()``, so importing it for
			// the side effect would silently do nothing. This assertion
			// catches a regression where someone reverts to the bare
			// import.
			expect(MAIN_SRC).not.toMatch(/import\s+["']\.\/lib\/tauri-bridge["'];?/);
		});
	});

	describe("bubble-main.tsx", () => {
		it('does NOT contain a static `import "./lib/tauri-bridge/install"`', () => {
			expect(BUBBLE_SRC).not.toMatch(
				/^\s*import\s+["']\.\/lib\/tauri-bridge\/install["'];?\s*$/m,
			);
		});

		it('contains a dynamic `import("./lib/tauri-bridge/install")`', () => {
			expect(BUBBLE_SRC).toMatch(
				/import\(\s*["']\.\/lib\/tauri-bridge\/install["']\s*\)/,
			);
		});

		it("gates the dynamic import on `window.__TAURI__?.core?.invoke`", () => {
			expect(BUBBLE_SRC).toMatch(/__TAURI__/);
			expect(BUBBLE_SRC).toMatch(/core/);
			expect(BUBBLE_SRC).toMatch(/invoke/);
		});

		it('does NOT contain a static `import "./lib/tauri-bridge"` (bare, no /install)', () => {
			expect(BUBBLE_SRC).not.toMatch(
				/import\s+["']\.\/lib\/tauri-bridge["'];?/,
			);
		});
	});
});
