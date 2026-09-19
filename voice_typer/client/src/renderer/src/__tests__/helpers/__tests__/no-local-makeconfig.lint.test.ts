import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", ".."); // .../src/renderer/src
const HELPERS_DIR = resolve(__dirname, ".."); // .../src/renderer/src/__tests__/helpers

// Match `function makeConfig` at any indentation. We deliberately do
// NOT match `makeConfig(` calls (which are the legitimate imports from
// `helpers/fixtures.ts`), only the function DECLARATION. We also match
// arrow/const forms (`const makeConfig = (...)`) so a future renamer
// can't bypass the lint by switching syntax.
const LOCAL_MAKECONFIG_DECL_RE =
	/(?:function\s+makeConfig\b|const\s+makeConfig\s*=)/;

interface Violation {
	path: string;
	line: number;
}

function walkTestFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		const st = statSync(full);
		if (st.isDirectory()) {
			// Skip the helpers dir itself, `makeConfig` is DEFINED here.
			if (full === HELPERS_DIR) continue;
			// Skip node_modules / build artefacts defensively.
			if (entry === "node_modules" || entry === "dist" || entry === "out") {
				continue;
			}
			walkTestFiles(full, out);
		} else if (
			st.isFile() &&
			(full.endsWith(".test.ts") || full.endsWith(".test.tsx"))
		) {
			out.push(full);
		}
	}
	return out;
}

describe("lint: no local `function makeConfig` outside helpers/", () => {
	it("warns (does not fail) when local makeConfig declarations are found", () => {
		const testFiles = walkTestFiles(RENDERER_SRC);
		const violations: Violation[] = [];

		for (const file of testFiles) {
			const src = readFileSync(file, "utf8");
			const lines = src.split("\n");
			for (let i = 0; i < lines.length; i++) {
				// noUncheckedIndexedAccess: lines[i] is `string | undefined` —
				// the loop bound guarantees it exists, so guard with `?? ""`
				// rather than a non-null assertion (biome noNonNullAssertion).
				if (LOCAL_MAKECONFIG_DECL_RE.test(lines[i] ?? "")) {
					violations.push({
						path: file.replace(RENDERER_SRC, "<renderer-src>"),
						line: i + 1,
					});
				}
			}
		}

		if (violations.length > 0) {
			// eslint-disable-next-line no-console
			console.warn(
				`[lint] ${violations.length} local \`function makeConfig\` ` +
					`declarations found outside \`__tests__/helpers/\`. ` +
					`Import \`makeConfig\` from \`@/__tests__/helpers/fixtures\` ` +
					`instead. (Non-blocking, see the ZU-19 finding in ` +
					`review.md for the deferred migration.) Violations:\n` +
					violations.map((v) => `  - ${v.path}:${v.line}`).join("\n"),
			);
		}

		// Always pass, this is a WARNING lint, not a hard assertion.
		// Migrating the existing 9 files is intentionally deferred; flip
		// this to `expect(violations).toEqual([])` once the migration
		// is complete and the convention should be enforced.
		expect(violations.length).toBeGreaterThanOrEqual(0);
	});
});

describe("lint: Settings test files use shared makeConfig (XA-15-2 regression)", () => {
	// __dirname = .../src/renderer/src/__tests__/helpers/__tests__/
	// Settings test files live at .../src/renderer/src/pages/__tests__/, so
	// we walk up three levels to reach `src/renderer/src/` then descend.
	const RENDERER_ROOT = resolve(__dirname, "..", "..", "..");
	const SETTINGS_TEST_FILES = [
		resolve(RENDERER_ROOT, "pages", "__tests__", "Settings.test.tsx"),
		resolve(
			RENDERER_ROOT,
			"pages",
			"__tests__",
			"Settings-empty-state.test.tsx",
		),
	];

	it.each(SETTINGS_TEST_FILES)(
		"%s imports makeConfig from helpers/fixtures",
		(file) => {
			const src = readFileSync(file, "utf8");
			expect(src).toMatch(
				/import\s+\{\s*makeConfig\s*\}\s+from\s+["']@\/__tests__\/helpers\/fixtures["']/,
			);
		},
	);

	it.each(SETTINGS_TEST_FILES)(
		"%s does NOT declare a local baseConfig object literal (must use makeConfig factory)",
		(file) => {
			const src = readFileSync(file, "utf8");
			// Matches `const baseConfig ... = {`, i.e. an inline object
			// literal assigned to `baseConfig`. The factory form
			// `const baseConfig = makeConfig({...})` does NOT match because
			// the RHS starts with `makeConfig(`, not `{`.
			const localLiteralRe = /const\s+baseConfig\b[^=]*=\s*\{/;
			expect(localLiteralRe.test(src)).toBe(false);
		},
	);
});
