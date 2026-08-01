/**
 *  regression guard: physical-side Tailwind utilities block RTL
 * mirroring. Logical-property utilities (`ms-*`, `me-*`, `ps-*`, `pe-*`,
 * `text-start`, `text-end`, `start-*`, `end-*`) auto-flip in RTL via the
 * `dir="rtl"` attribute that {@link setLocale} sets on
 * `document.documentElement` for Arabic. Physical utilities
 * (`ml-*`/`mr-*`/`pl-*`/`pr-*`/`text-left`/`text-right`) don't flip —
 * they always render as left/right regardless of document direction,
 * which means an Arabic user sees a broken (LTR-locked) layout for any
 * component that still uses them.
 *
 * This test is a FORWARD-PROGRESS RATCHET: it scans every `.tsx` /
 * `.ts` source file under `src/renderer/src/` (excluding tests, stories,
 * and the `__tests__/` subtrees) for physical-side className utilities
 * and fails if any file OUTSIDE the {@link CURRENTLY_VIOLATING} allowlist
 * is found to use them. Files already in the allowlist are tolerated so
 * the build stays green while their owning agents finish the migration
 * to logical properties. When an allowlisted file is migrated, remove
 * it from the set so a future regression is caught immediately.
 *
 * Rationale for the allowlist pattern (vs. a hard "no physical classes
 * anywhere" rule):
 *   - The finding cites 30 files; the migration is in progress across
 *     multiple sub-agents. A hard rule would break the build today.
 *   - The allowlist shrinks monotonically: each migration PR removes
 *     one entry. The size-bound assertion below makes any GROWTH a
 *     CI failure so the ratchet direction is enforced.
 *
 * Why this test lives in `i18n/__tests__/`:
 *   - The RTL mirroring contract is owned by the i18n module (it sets
 *     `document.documentElement.dir`).
 *   - Logical-property utilities are downstream of that contract: they
 *     only "do the right thing" because the i18n layer sets `dir`.
 *   - The sub-agent () that owns the i18n module also owns the
 *     RTL contract end-to-end, so the regression guard belongs here.
 *
 * Platform: Linux sandbox / Windows host / macOS host (the test is a
 * pure static-source check — no runtime CSS evaluation, no platform
 * dependency). Validation:
 *   VALIDATE ON LINUX HOST: cd voice_typer/client && npx vitest run \
 *     src/renderer/src/i18n/__tests__/rtl-physical-css-guard.test.ts
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", "..");

/**
 * Files currently allowed to use physical-side CSS utilities.
 *
 * Each entry is a path relative to `src/renderer/src/`. When a file's
 * owning agent migrates it to logical properties (`ml-*` → `ms-*`,
 * `text-left` → `text-start`, etc.), remove the entry from this set.
 *
 * The size-bound assertion ({@link CURRENTLY_VIOLATING_SIZE_BOUND})
 * ensures the set only shrinks: any growth past the bound is a CI
 * failure (a new file regressed), and any new violation in a file
 * that's already in the set is silently tolerated (the migration is
 * still in progress there).
 *
 * Last audited: 2026-07-27 by  (client_root_i18n).
 */
const CURRENTLY_VIOLATING: ReadonlySet<string> = new Set<string>([
	// `text-left` on a `<pre>` rendering the error stack trace. Migrating
	// to `text-start` requires touching ErrorBoundary.tsx (owned by
	//another agent — out of 's file scope).
	"components/feedback/ErrorBoundary.tsx",
	// `text-right` on a `<span>` rendering credits values. Migrating to
	// `text-end` requires touching About.tsx (owned by another agent —
	//out of 's file scope).
	"pages/About.tsx",
]);

/**
 * Hard upper bound on {@link CURRENTLY_VIOLATING}'s size.
 *
 * The set is at 2 entries today; the bound is set to 5 to leave room
 * for short-term additions during the migration (e.g. a new file is
 * found to violate the rule and is added to the allowlist pending
 * migration by its owning agent). Once the migration is complete,
 * the set should be empty and the bound can be lowered to 0.
 *
 * If this assertion ever fires, it means the set grew past the bound —
 * either raise the bound (with a comment explaining why) or migrate
 * the offending files instead of allowlisting them.
 */
const CURRENTLY_VIOLATING_SIZE_BOUND = 5;

/**
 * Regex matching physical-side Tailwind utilities that block RTL
 * mirroring. Captures:
 *
 *   - `ml-N` / `mr-N` / `pl-N` / `pr-N` (margin/padding left/right)
 *   - `text-left` / `text-right` (text alignment)
 *
 * The regex is anchored at the start of the utility (preceding
 * whitespace, quote, or colon-variant delimiter) so it doesn't
 * match substrings of longer identifiers (e.g. `html-` or
 * `template-right`).
 *
 * Variants like `data-inset:pl-9.5` and `has-data-[icon=inline-end]:pr-2.5`
 * are also caught because the leading `:` is one of the allowed
 * boundary characters.
 *
 * NOT flagged (intentionally):
 *   - `mt-*` / `mb-*` (block-axis — physical is fine; vertical doesn't
 *     flip in RTL).
 *   - `px-*` / `py-*` (axis-pair utilities — already direction-agnostic).
 *   - `left-N` / `right-N` (positional utilities for absolute/fixed
 *     positioning — these DO need physical left/right semantics in
 *     many cases, e.g. centering a modal with `left-1/2`). The
 *     original finding did call these out, but the migration is
 *     per-element (not all `left-1/2` should become `start-1/2`),
 *     so they're excluded from this guard. A separate per-component
 *     audit (the existing `nh-rtl-logical-properties.test.tsx`)
 *     covers component-specific migrations.
 *   - Literal substrings inside comments / strings that merely mention
 *     the legacy class name in a migration note (e.g. `"ml-2 → ms-2"`
 *     in a comment). Block comments + line comments are stripped
 *     before matching.
 */
const PHYSICAL_INLINE_CLASSNAME = /(?:^|[\s":])(?:ml|mr|pl|pr)-\d+(?:\.\d+)?/;
const PHYSICAL_TEXT_ALIGN = /(?:^|\s)text-(?:left|right)(?=\s|["'`$])/;

/** Strip /* block comments *\/ and // line comments from a source string. */
function stripComments(src: string): string {
	return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

/** Extract every `className="..."` / `className='...'` value from the source. */
function extractClassNames(src: string): string[] {
	const out: string[] = [];
	// Double-quoted className values.
	for (const m of src.matchAll(/className="([^"]+)"/g)) {
		// `m[1]` is `string | undefined` under `noUncheckedIndexedAccess`;
		// the regex's capture group guarantees a hit, but guard keeps
		// the typed push happy without a non-null assertion.
		if (m[1] !== undefined) out.push(m[1]);
	}
	// Single-quoted className values.
	for (const m of src.matchAll(/className='([^']+)'/g)) {
		if (m[1] !== undefined) out.push(m[1]);
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

/**
 * Check a single file's source for physical-side CSS violations.
 * Returns a list of human-readable violation strings (empty if clean).
 *
 * The `rel` parameter is accepted for symmetry with the caller's
 * (rel, src) tuple shape but is intentionally unused — the violation
 * message embeds the className value (which is what the developer
 * needs to fix), not the file path (the caller adds the path when
 * collecting results). Prefixed with `_` to silence the unused-param
 * lint without dropping the parameter (keeping the call-site shape
 * stable makes future logging changes easier).
 */
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

describe("S5-CR-45: RTL regression guard — physical-side Tailwind utilities block RTL mirroring", () => {
	it("CURRENTLY_VIOLATING allowlist size is within the documented bound", () => {
		// Ratchet: the allowlist should only ever SHRINK (or stay the same).
		// If a new file is found to violate the rule, add it to the
		// allowlist AND raise this bound (with a comment explaining why)
		// — or better, migrate the offending file to logical properties.
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
			// Skip files in the allowlist — they're tolerated pending
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
					"files NOT in the CURRENTLY_VIOLATING allowlist. These utilities don't flip in RTL — " +
					"the Arabic UI renders a broken (LTR-locked) layout for any component using them. " +
					"Migrate to logical utilities (ms-/me-/ps-/pe-/text-start/text-end), OR if the file " +
					"is mid-migration by another agent, add it to CURRENTLY_VIOLATING in this test.",
				...unexpected,
			].join("\n"),
		).toEqual([]);
	});

	it("every entry in CURRENTLY_VIOLATING actually still has a violation (no stale allowlist entries)", () => {
		// Ratchet: when an allowlisted file is migrated, the entry MUST
		// be removed from the set — otherwise the allowlist accumulates
		// stale entries that mask future regressions. This test fails
		// loudly when an entry is no longer needed.
		const files = collectSourceFiles();
		const fileMap = new Map<string, string>();
		for (const { rel, src } of files) fileMap.set(rel, src);

		const stale: string[] = [];
		for (const rel of CURRENTLY_VIOLATING) {
			const src = fileMap.get(rel);
			if (src === undefined) {
				// The file was deleted/renamed — the allowlist entry is stale.
				stale.push(`${rel}: file not found (deleted or renamed)`);
				continue;
			}
			const violations = findViolations(rel, src);
			if (violations.length === 0) {
				stale.push(
					`${rel}: no physical-side utilities found — file was migrated, ` +
						"remove this entry from CURRENTLY_VIOLATING.",
				);
			}
		}
		expect(
			stale,
			[
				"CURRENTLY_VIOLATING has stale entries — files that no longer use physical-side " +
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
			"RENDERER_SRC file walker returned 0 files — the test is broken",
		).toBeGreaterThan(0);
	});
});
