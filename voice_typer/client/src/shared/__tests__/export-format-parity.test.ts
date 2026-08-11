/**
 * VP-16 regression guard: no production code may redeclare the inline
 * `"json" | "csv"` union.
 *
 * `ExportFormat` (defined in `src/shared/export-format.ts`) is the
 * single source of truth for the export-format union. Before VP-16
 * the bare union `"json" | "csv"` was inlined at 12+ call sites
 * (main-process export handlers, preload bridge types, the renderer's
 * ExportFormatMenu, the per-page toolbar/import-export hooks, and the
 * tauri window namespace). Adding a new format (e.g. `"tsv"`) required
 * touching every site with no compile-time guard; a missed file
 * silently broke the format selector for that flow.
 *
 * This test scans the PRODUCTION source tree (main, preload, shared,
 * renderer) for the inline pattern and fails if any remains. It is
 * deliberately conservative:
 *   - Only literal `"json" | "csv"` (and `"csv" | "json"`) unions are
 *     flagged — the exact duplication VP-16 removed.
 *   - Test files (`.test.ts` / `.test.tsx` / `__tests__/`) are
 *     EXCLUDED: tests may construct fixture values inline, and the
 *     2 remaining inline unions in `tauri-bridge-commands.test.ts`
 *     are exactly the kind of test-only drift this guard is meant to
 *     catch without blocking test authors from writing literals.
 *   - `src/shared/export-format.ts` itself is excluded (it is the
 *     canonical declaration).
 *
 * A production file that legitimately needs the union must import
 * `ExportFormat` from `src/shared/export-format` instead of inlining.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

const CLIENT_SRC = join(__dirname, "..", "..", "..");
const EXCLUDED_BASE = "src/shared/export-format.ts";
const TEST_SUFFIXES = [".test.ts", ".test.tsx"];

// Matches the inline `"json" | "csv"` union (either order). The
// whitespace-tolerant shape mirrors how the pre-VP-16 sites wrote it
// (e.g. `"json" | "csv"` in type position). Only the two-literal union
// is flagged — a single `"json"` literal or a wider union is not this
// finding's target.
const INLINE_UNION_RE =
	/["']json["']\s*\|\s*["']csv["']|["']csv["']\s*\|\s*["']json["']/;

function collectTsFiles(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
		const full = join(dir, entry.name);
		if (entry.isDirectory()) {
			collectTsFiles(full, out);
		} else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) {
			out.push(full);
		}
	}
	return out;
}

describe('VP-16: no inline "json" | "csv" union in production code', () => {
	it("scans a non-empty production TS/TSX tree (sanity)", () => {
		const files = collectTsFiles(CLIENT_SRC);
		const prodFiles = files.filter(
			(f) =>
				!TEST_SUFFIXES.some((s) => f.endsWith(s)) &&
				!f.includes("__tests__") &&
				relative(CLIENT_SRC, f).replace(/\\/g, "/") !== EXCLUDED_BASE,
		);
		// The tree is large (main + preload + shared + renderer). If
		// this drops to zero the root path is wrong and the test would
		// silently pass.
		expect(prodFiles.length).toBeGreaterThan(100);
	});

	it("no production file redeclares the inline union", () => {
		const violations: string[] = [];
		const files = collectTsFiles(CLIENT_SRC);
		for (const file of files) {
			const rel = relative(CLIENT_SRC, file).replace(/\\/g, "/");
			if (rel === EXCLUDED_BASE) continue; // canonical declaration
			if (TEST_SUFFIXES.some((s) => file.endsWith(s))) continue;
			if (file.includes("__tests__")) continue;
			const text = readFileSync(file, "utf8");
			const lines = text.split("\n");
			for (let i = 0; i < lines.length; i++) {
				// Skip comment-only lines — a docstring/comment that
				// mentions the old union (e.g. this very test's header)
				// is documentation, not a redeclaration.
				const raw = lines[i] ?? "";
				const line = raw.trim();
				if (
					line.startsWith("//") ||
					line.startsWith("*") ||
					line.startsWith("/*")
				) {
					continue;
				}
				if (INLINE_UNION_RE.test(line)) {
					violations.push(`${rel}:${i + 1}: ${raw.trim()}`);
				}
			}
		}
		expect(
			violations,
			'inline "json" | "csv" unions must be imported from ' +
				"src/shared/export-format.ts (VP-16). Found:\n" +
				violations.join("\n"),
		).toEqual([]);
	});
});
