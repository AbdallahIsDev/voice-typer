/**
 * regression test: the Tauri-bridge install gate MUST stay a
 * RUNTIME-GATED DYNAMIC ``import()`` — never a static top-level import.
 *
 * Background: the original code had ``import "./lib/tauri-bridge/install"``
 * as a static top-level import in both entrypoints. Under Electron the
 * preload script (``src/preload/index.ts:19-117``) already installs
 * ``window.python`` / ``window.bubble`` / ``window.window_`` via
 * ``contextBridge.exposeInMainWorld``, so the ``install.ts`` module
 * was shipped but never executed under Electron — pure bundle bloat.
 *
 * The gate now lives in ONE shared module,
 * ``lib/tauri-bridge/ensure.ts`` (``ensureTauriBridgeInstalled()``) —
 * previously the gate block and its rationale comment were duplicated
 * in ``main.tsx`` and ``bubble-main.tsx``. The entrypoints import the
 * ensure module statically (it is dependency-light: it only imports
 * ``./detect``) and call ``await ensureTauriBridgeInstalled()`` at
 * top level. The ensure module does the gated dynamic import:
 *
 *   if (isTauri()) {
 *       await import("./install");
 *   }
 *
 * Vite emits ``install.ts`` as a SEPARATE async chunk, fetched only
 * when the renderer runs inside a Tauri WebView. Under Electron the
 * gate is false and the chunk is never fetched.
 *
 * This test does a STATIC source analysis (reads the files as text and
 * regex-matches) rather than importing the entrypoints — importing
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
const ENSURE_SRC = readFileSync(
	resolve(__dirname, "../lib/tauri-bridge/ensure.ts"),
	"utf-8",
);

describe("tauri-bridge install is a runtime-gated dynamic import", () => {
	describe("lib/tauri-bridge/ensure.ts (the shared gate module)", () => {
		it('contains a dynamic `import("./install")`', () => {
			// The dynamic form is `import("./install")` (parenthesis
			// immediately after `import`, no whitespace). The optional
			// `await ` prefix is not matched here — we only assert the
			// dynamic-import syntax is present.
			expect(ENSURE_SRC).toMatch(/import\(\s*["']\.\/install["']\s*\)/);
		});

		it("gates the dynamic import on isTauri() (the __TAURI__.core.invoke check)", () => {
			// The runtime gate is what makes the chunk "only fetched under
			// Tauri". Without the gate, Vite would still code-split the
			// chunk, but the ensure module would unconditionally fetch it
			// (defeating the purpose under Electron).
			expect(ENSURE_SRC).toMatch(/isTauri\(\)/);
		});

		it("stays dependency-light: no static import of the bridge barrel or the Tauri API", () => {
			// If `ensure.ts` statically imported `./index` (the bridge
			// barrel) or `@tauri-apps/*`, statically importing
			// `ensureTauriBridgeInstalled` from the entrypoints would drag
			// the whole install graph back into the eager bundle — the
			// exact regression this architecture prevents.
			expect(ENSURE_SRC).not.toMatch(/import\s+["']\.\/index["']/);
			expect(ENSURE_SRC).not.toMatch(
				/import\s+[^"' ]*\s*from\s*["']@tauri-apps/,
			);
			expect(ENSURE_SRC).not.toMatch(/import\s+["']@tauri-apps/);
		});
	});

	for (const [name, src] of [
		["main.tsx", MAIN_SRC],
		["bubble-main.tsx", BUBBLE_SRC],
	] as const) {
		describe(name, () => {
			it("imports and top-level-awaits the shared ensureTauriBridgeInstalled()", () => {
				expect(src).toMatch(
					/import\s*\{\s*ensureTauriBridgeInstalled\s*\}\s*from\s*["']\.\/lib\/tauri-bridge\/ensure["']/,
				);
				expect(src).toMatch(/await\s+ensureTauriBridgeInstalled\(\)/);
			});

			it('does NOT contain a static `import "./lib/tauri-bridge/install"`', () => {
				// A static side-effect import looks like:
				//   import "./lib/tauri-bridge/install";
				// (whitespace between `import` and the string literal, NOT
				// `import(`). This is the previous pattern that pulled the
				// install graph into the Electron bundle. The `m` flag makes
				// `^` / `$` match line boundaries; the optional leading
				// whitespace + optional trailing semicolon catch indented /
				// semicolon-less variants.
				expect(src).not.toMatch(
					/^\s*import\s+["']\.\/lib\/tauri-bridge\/install["'];?\s*$/m,
				);
				// A static NAMED import from the install module is the same
				// regression via a different syntax.
				expect(src).not.toMatch(
					/import\s+\{[^}]*\}\s*from\s*["']\.\/lib\/tauri-bridge\/install["']/,
				);
			});

			it('does NOT contain a static `import "./lib/tauri-bridge"` (bare, no /install)', () => {
				// The bare `./lib/tauri-bridge` side-effect import (without
				// `/install`) is the pre-split pattern — `index.ts` no longer
				// auto-invokes ``installTauriBridge()``, so importing it for
				// the side effect would silently do nothing. This assertion
				// catches a regression where someone reverts to the bare
				// import.
				expect(src).not.toMatch(/import\s+["']\.\/lib\/tauri-bridge["'];?/);
			});
		});
	}
});
