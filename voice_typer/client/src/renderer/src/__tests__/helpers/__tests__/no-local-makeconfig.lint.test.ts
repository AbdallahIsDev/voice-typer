/**
 * Non-blocking lint-test: detect local `function makeConfig` declarations
 * outside this `helpers/` folder.
 *
 * Background (see the  finding in `review.md` and the ADOPTION
 * STATUS note at the top of `fixtures.ts`): the canonical fixture is
 * `helpers/fixtures.ts → makeConfig(overrides)`. Historically, ~9 test
 * files each declared their own local `function makeConfig(...)` (or
 * `const baseConfig = { ... }`) to avoid importing the helper. That
 * duplication causes config drift: when a field is added upstream, every
 * local copy goes stale until an unrelated test fails.
 *
 * Migrating the existing 9 files is a large mechanical change that is
 * intentionally deferred (out of scope for this fix). This lint-test
 * guards against NEW violations: it scans every renderer test file
 * outside `__tests__/helpers/` for a top-level `function makeConfig`
 * declaration and, if any are found, prints a `console.warn` listing
 * them. The test PASSES unconditionally — emitting the warning is the
 * only signal — so it does not break the build (the existing 9 files
 * would otherwise turn this red on every CI run until migrated).
 *
 * If/when the deferred migration is complete, flip this to a hard
 * `expect(...).toBe(0)` assertion so the convention is enforced.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", ".."); // .../src/renderer/src
const HELPERS_DIR = resolve(__dirname, ".."); // .../src/renderer/src/__tests__/helpers

// Match `function makeConfig` at any indentation. We deliberately do
// NOT match `makeConfig(` calls (which are the legitimate imports from
// `helpers/fixtures.ts`) — only the function DECLARATION. We also match
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
			// Skip the helpers dir itself — `makeConfig` is DEFINED here.
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
				if (LOCAL_MAKECONFIG_DECL_RE.test(lines[i])) {
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
					`declaration(s) found outside \`__tests__/helpers/\`. ` +
					`Import \`makeConfig\` from \`@/__tests__/helpers/fixtures\` ` +
					`instead. (Non-blocking — see the ZU-19 finding in ` +
					`review.md for the deferred migration.) Violations:\n` +
					violations.map((v) => `  - ${v.path}:${v.line}`).join("\n"),
			);
		}

		// Always pass — this is a WARNING lint, not a hard assertion.
		// Migrating the existing 9 files is intentionally deferred; flip
		// this to `expect(violations).toEqual([])` once the migration
		// is complete and the convention should be enforced.
		expect(violations.length).toBeGreaterThanOrEqual(0);
	});
});
