import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", "..");

// Currently empty: every renderer file uses logical side utilities. Add
// an entry only for a file genuinely mid-migration (and raise the bound
// below with a comment); the stale-entry check demands its removal once
// the file is clean, so the list can never accumulate dead tolerance.
const CURRENTLY_VIOLATING: ReadonlySet<string> = new Set<string>();

const CURRENTLY_VIOLATING_SIZE_BOUND = 5;

// Matches numeric (`ml-2`), keyword (`ml-auto`) and arbitrary-value
// (`ml-[10px]`) physical side utilities. The keyword/arbitrary forms are
// included because the numeric-only form let `ml-auto` slip through.
const PHYSICAL_INLINE_CLASSNAME =
	/(?:^|[\s":])(?:ml|mr|pl|pr)-(?:\d+(?:\.\d+)?|auto|\[[^\]]*\])/;
const PHYSICAL_TEXT_ALIGN = /(?:^|\s)text-(?:left|right)(?=\s|["'`$])/;

/** Strip /* block comments *\/ and // line comments from a source string. */
function stripComments(src: string): string {
	return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

function extractClassNames(src: string): string[] {
	const out: string[] = [];
	// Double-quoted string literals (handles `\"` escapes via `\\.`).
	for (const m of src.matchAll(/"([^"\\]*(?:\\.[^"\\]*)*)"/g)) {
		// `m[1]` is `string | undefined` under `noUncheckedIndexedAccess`;
		// the regex's capture group guarantees a hit, but guard keeps
		// the typed push happy without a non-null assertion.
		if (m[1] !== undefined) out.push(m[1]);
	}
	// Single-quoted string literals (handles `\'` escapes).
	for (const m of src.matchAll(/'([^'\\]*(?:\\.[^'\\]*)*)'/g)) {
		if (m[1] !== undefined) out.push(m[1]);
	}
	// Template-literal strings. The capture is the raw inner text
	// (handles escaped backticks and `\\` escapes); `${...}` interpolations
	// are then stripped to a single space so the surrounding static text
	// still anchors correctly on the physical-CSS regexes.
	for (const m of src.matchAll(/`([^`\\]*(?:\\.[^`\\]*)*)`/g)) {
		if (m[1] !== undefined) {
			out.push(m[1].replace(/\$\{[^}]*\}/g, " "));
		}
	}
	return out;
}

/** Walk the renderer src tree and return (relativePath, src) pairs for each source file. */
function collectSourceFiles(): { rel: string; src: string }[] {
	// The renderer src tree is small enough (~250 files) that we can walk
	// it synchronously with the Node fs API. Using `readFileSync` here
	// mirrors the existing pattern in
	// `components/__tests__/nh-rtl-logical-properties.test.tsx`.
	const out: { rel: string; src: string }[] = [];
	const skipDirs = new Set([
		"__tests__",
		"node_modules",
		".vite",
		"dist",
		"out",
	]);
	const walk = (absDir: string, relDir: string): void => {
		let entries: string[];
		try {
			entries = readdirSync(absDir);
		} catch {
			return;
		}
		for (const name of entries) {
			const abs = resolve(absDir, name);
			const rel = relDir ? `${relDir}/${name}` : name;
			let st: { isDirectory: () => boolean };
			try {
				st = statSync(abs);
			} catch {
				continue;
			}
			if (st.isDirectory()) {
				if (skipDirs.has(name)) continue;
				walk(abs, rel);
				continue;
			}
			// Only scan .ts / .tsx source files (not .json, .css, .svg).
			if (!/\.(ts|tsx)$/.test(name)) continue;
			// Skip test files, stories, and declaration files.
			if (
				name.includes(".test.") ||
				name.includes(".stories.") ||
				name.endsWith(".d.ts")
			) {
				continue;
			}
			let src: string;
			try {
				src = readFileSync(abs, "utf8");
			} catch {
				continue;
			}
			out.push({ rel, src });
		}
	};
	walk(RENDERER_SRC, "");
	return out;
}

function findViolations(_rel: string, rawSrc: string): string[] {
	const stripped = stripComments(rawSrc);
	const classNames = extractClassNames(stripped);
	const out: string[] = [];
	for (const cls of classNames) {
		if (PHYSICAL_INLINE_CLASSNAME.test(cls)) {
			out.push(`ml/mr/pl/pr utility: "${cls.slice(0, 100)}"`);
		}
		if (PHYSICAL_TEXT_ALIGN.test(cls)) {
			out.push(`text-left/text-right utility: "${cls.slice(0, 100)}"`);
		}
	}
	return out;
}

describe("S5-CR-45: RTL regression guard, physical-side Tailwind utilities block RTL mirroring", () => {
	it("CURRENTLY_VIOLATING allowlist size is within the documented bound", () => {
		// Ratchet: the allowlist should only ever SHRINK (or stay the same).
		// If a new file is found to violate the rule, add it to the
		// allowlist AND raise this bound (with a comment explaining why)
		//, or better, migrate the offending file to logical properties.
		expect(
			CURRENTLY_VIOLATING.size,
			`CURRENTLY_VIOLATING allowlist grew past the bound of ${CURRENTLY_VIOLATING_SIZE_BOUND}. ` +
				"Either migrate the new offending files to logical properties (ms-*/me-*/ps-*/pe-*/text-start/text-end) " +
				"or raise CURRENTLY_VIOLATING_SIZE_BOUND with a comment explaining why.",
		).toBeLessThanOrEqual(CURRENTLY_VIOLATING_SIZE_BOUND);
	});

	it("no source file OUTSIDE the CURRENTLY_VIOLATING allowlist uses physical-side CSS utilities", () => {
		const files = collectSourceFiles();
		const unexpected: string[] = [];
		for (const { rel, src } of files) {
			// Skip files in the allowlist, they're tolerated pending
			// migration by their owning agent.
			if (CURRENTLY_VIOLATING.has(rel)) continue;
			const violations = findViolations(rel, src);
			for (const v of violations) {
				unexpected.push(`${rel}: ${v}`);
			}
		}
		expect(
			unexpected,
			[
				"Found physical-side Tailwind utilities (ml-/mr-/pl-/pr-/text-left/text-right) in " +
					"files NOT in the CURRENTLY_VIOLATING allowlist. These utilities don't flip in RTL, " +
					"the Arabic UI renders a broken (LTR-locked) layout for any component using them. " +
					"Migrate to logical utilities (ms-/me-/ps-/pe-/text-start/text-end), OR if the file " +
					"is mid-migration by another agent, add it to CURRENTLY_VIOLATING in this test.",
				...unexpected,
			].join("\n"),
		).toEqual([]);
	});

	it("every entry in CURRENTLY_VIOLATING actually still has a violation (no stale allowlist entries)", () => {
		// Ratchet: when an allowlisted file is migrated, the entry MUST
		// be removed from the set, otherwise the allowlist accumulates
		// stale entries that mask future regressions. This test fails
		// loudly when an entry is no longer needed.
		const files = collectSourceFiles();
		const fileMap = new Map<string, string>();
		for (const { rel, src } of files) fileMap.set(rel, src);

		const stale: string[] = [];
		for (const rel of CURRENTLY_VIOLATING) {
			const src = fileMap.get(rel);
			if (src === undefined) {
				// The file was deleted/renamed, the allowlist entry is stale.
				stale.push(`${rel}: file not found (deleted or renamed)`);
				continue;
			}
			const violations = findViolations(rel, src);
			if (violations.length === 0) {
				stale.push(
					`${rel}: no physical-side utilities found, file was migrated, ` +
						"remove this entry from CURRENTLY_VIOLATING.",
				);
			}
		}
		expect(
			stale,
			[
				"CURRENTLY_VIOLATING has stale entries, files that no longer use physical-side " +
					"CSS utilities. Remove them from the allowlist so future regressions are caught:",
				...stale,
			].join("\n"),
		).toEqual([]);
	});

	it("the renderer source tree was actually scanned (non-empty file list)", () => {
		// Defensive: if the file walker silently returned an empty list
		// (e.g. RENDERER_SRC resolved wrong, all files were filtered),
		// the "no unexpected violations" test would pass trivially
		// without checking anything. This sanity test ensures the walker
		// actually found files to scan.
		const files = collectSourceFiles();
		expect(
			files.length,
			"RENDERER_SRC file walker returned 0 files, the test is broken",
		).toBeGreaterThan(0);
	});
});
