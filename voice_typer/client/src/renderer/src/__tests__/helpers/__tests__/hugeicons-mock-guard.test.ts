/**
 * Drift guard for the shared `@hugeicons/core-free-icons` mock.
 *
 * Background: every renderer test file used to hand-roll its own icon-module
 * `vi.mock` factory with `IconX: make("IconX")` stub lists. The lists
 * drifted — a component importing an icon missing from a file's list crashed
 * that file's tests at module-load time with "No '<Icon>' export is defined
 * on the mock" (vitest validates named imports against the mock factory
 * upfront). The canonical mock now lives in `helpers/hugeicons-mock.ts`;
 * these HARD assertions keep it in sync with the source tree forever:
 *
 *   1. Every icon imported from `@hugeicons/core-free-icons` anywhere in
 *      `src/renderer/src` (components, pages, stories, tests) MUST be a key
 *      of the canonical mock. Adding a new icon to a component → this test
 *      fails with a message pointing at `hugeicons-mock.ts`, so the mock
 *      can never silently miss an icon again.
 *
 *   2. No test file may hand-roll its own `...Icon: make("...")` stub list —
 *      they must all delegate to `createHugeiconsMock()`, so behavior is
 *      consistent everywhere and there is exactly one list to maintain.
 *
 *   3. The canonical mock must stay alphabetized (keeps diffs greppable).
 *
 * NOTE: this file deliberately never contains the literal mock-marker
 * substring (the icon-module specifier immediately following `vi.mock(`)
 * — the one-off migration script that converted the hand-rolled mocks
 * scans for that substring and would otherwise rewrite this file's own
 * docstring example.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { hugeiconsMockKeys } from "../hugeicons-mock";

const RENDERER_SRC = resolve(__dirname, "..", "..", ".."); // .../src/renderer/src
const HELPERS_DIR = resolve(__dirname, ".."); // .../src/renderer/src/__tests__/helpers

/** `import { A, B as C, ... } from "@hugeicons/core-free-icons"` (multi-line OK). */
const HUGEICONS_IMPORT_RE =
	/import\s*\{([^}]*)\}\s*from\s*["']@hugeicons\/core-free-icons["']/g;

/** A hand-rolled stub entry: `SomeIcon: make("SomeIcon")`. */
const HAND_ROLLED_STUB_RE = /\b[A-Za-z0-9]+Icon\s*:\s*make\s*\(/;

/** Remove /* block comments and // line comments so doc examples can't
 *  produce false positives in the import scan. */
function stripComments(src: string): string {
	const noBlocks = src.replace(/\/\*[\s\S]*?\*\//g, " ");
	// Only treat `//` as a comment when NOT part of a `://` URL or inside
	// a string (best-effort: URLs in prose are the only common false hit,
	// and a truncated code line can never fabricate a hugeicons import).
	return noBlocks.replace(/(^|[^:])[ \t]*\/\/.*$/gm, "$1");
}

function walkFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		const st = statSync(full);
		if (st.isDirectory()) {
			if (
				entry === "node_modules" ||
				entry === "dist" ||
				entry === "out" ||
				entry === ".vite"
			) {
				continue;
			}
			walkFiles(full, out);
		} else if (st.isFile() && (full.endsWith(".ts") || full.endsWith(".tsx"))) {
			out.push(full);
		}
	}
	return out;
}

/** Extract the (unaliased) icon names imported from the hugeicons module. */
function importedIconNames(src: string): string[] {
	const names = new Set<string>();
	for (const match of src.matchAll(HUGEICONS_IMPORT_RE)) {
		const body = match[1] ?? "";
		for (const raw of body.split(",")) {
			const ident = raw
				.trim()
				.split(/\s+as\s+/)[0]
				?.trim();
			if (ident && /^[A-Za-z0-9_$]+$/.test(ident)) {
				names.add(ident);
			}
		}
	}
	return [...names];
}

describe("hugeicons mock drift guard", () => {
	it("every icon imported from @hugeicons/core-free-icons is in the canonical mock", () => {
		// Self-validating path resolution (mirrors the do-mock drift
		// guard): if the __dirname math drifts, fail loudly instead of
		// silently under-scanning — this file previously resolved
		// RENDERER_SRC to .../src/renderer/src/__tests__ (one `..` too
		// few) and missed icon imports in components/pages.
		expect(
			existsSync(join(RENDERER_SRC, "App.tsx")),
			`[guard] RENDERER_SRC misresolved: ${RENDERER_SRC}`,
		).toBe(true);

		const missing: Array<{ path: string; icon: string }> = [];
		for (const file of walkFiles(RENDERER_SRC)) {
			const src = stripComments(readFileSync(file, "utf8"));
			for (const icon of importedIconNames(src)) {
				if (!hugeiconsMockKeys.includes(icon)) {
					missing.push({
						path: file.replace(RENDERER_SRC, "<renderer-src>"),
						icon,
					});
				}
			}
		}
		// Second arg = assertion message: it IS displayed when the
		// assertion fails, so the fix path surfaces instead of dying in
		// an unreachable console.error.
		expect(
			missing,
			`[guard] icons missing from @/__tests__/helpers/hugeicons-mock: ` +
				missing.map((m) => `${m.icon} (${m.path})`).join(", ") +
				`. Add them to hugeicons-mock.ts and rerun.`,
		).toEqual([]);
	});

	it("no test file hand-rolls its own icon stub list (must use createHugeiconsMock)", () => {
		const offenders: Array<{ path: string; line: number }> = [];
		for (const file of walkFiles(RENDERER_SRC)) {
			// The canonical mock itself defines the `make` stubs.
			if (file.startsWith(HELPERS_DIR)) continue;
			const src = readFileSync(file, "utf8");
			let inBlockComment = false;
			for (const [i, rawLine] of src.split("\n").entries()) {
				const line = rawLine ?? "";
				if (inBlockComment) {
					if (line.includes("*/")) inBlockComment = false;
					continue;
				}
				// Skip multi-line `/* ... */` spans (per-line stripping can't
				// see across lines, so track the state explicitly) — a
				// docstring line like ` * SomeIcon: make("SomeIcon")` must
				// not register as a hand-rolled stub.
				if (line.includes("/*") && !line.includes("*/")) {
					inBlockComment = true;
					continue;
				}
				if (HAND_ROLLED_STUB_RE.test(stripComments(line))) {
					offenders.push({
						path: file.replace(RENDERER_SRC, "<renderer-src>"),
						line: i + 1,
					});
				}
			}
		}
		// Second arg = assertion message; surfaces the offender list with
		// the fix path when the assertion fails.
		expect(
			offenders,
			`[guard] hand-rolled icon stubs found — delegate to ` +
				`createHugeiconsMock() from @/__tests__/helpers/hugeicons-mock ` +
				`(import it inside the icon-module vi.mock factory) instead. ` +
				`Offenders:\n` +
				offenders.map((o) => `  - ${o.path}:${o.line}`).join("\n"),
		).toEqual([]);
	});

	it("canonical mock is alphabetized (keeps diffs greppable)", () => {
		const sorted = [...hugeiconsMockKeys].sort((a, b) => a.localeCompare(b));
		expect(hugeiconsMockKeys).toEqual(sorted);
	});
});
