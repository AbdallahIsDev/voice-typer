/**
 * Regression test pinning the useStatsShare logging invariant:
 *   "Production console.info calls in useStatsShare.ts"
 *
 * This file replaces an earlier dead debug-only spec (4 console.log
 * calls + `expect(true).toBe(true)`). It asserts nothing vacuous; it
 * pins a real production-code invariant:
 *
 *   Every `console.info(...)` call in `useStatsShare.ts` MUST be wrapped in
 *   an `if (import.meta.env.DEV) { ... }` block so the diagnostic logs do
 *   not leak user-data shape (offsetWidth / dimensions / dataUrl prefix)
 *   to the renderer DevTools console of the packaged app.
 *
 * Static-source-check strategy
 * ----------------------------
 * Rendering the `useStatsShare` hook (it pulls in `html-to-image`, `react`,
 * the i18n singleton, etc.) would force us to re-stub a heavy dependency
 * graph for what is fundamentally a source-text invariant. The
 * `console.info` calls and their `import.meta.env.DEV` wrappers are both
 * visible in the source text, so we use `fs.readFileSync` + a small block
 * parser — same pattern used by `Dashboard.test.tsx` and
 * `pages-improvements.test.tsx` for similar static contracts.
 *
 * The parser:
 *   1. Strips string/template/comment contents (replacing them with spaces
 *      of the same length so line numbers and brace positions are
 *      preserved). This eliminates false-positive braces from `${...}`
 *      template interpolations, object-literal string keys, and regex
 *      literals.
 *   2. Walks the stripped source character-by-character, maintaining a
 *      stack of "block frames". Each frame records the brace depth at
 *      which it was opened and whether it was opened by an
 *      `if (import.meta.env.DEV)` header.
 *   3. When a `console.info(` token is found, the parser checks the
 *      innermost enclosing block frame. If any enclosing frame is a DEV
 *      block, the call is "gated".
 */
import { describe, expect, it } from "vitest";

const fs = require("node:fs");
const path = require("node:path");

const USE_STATS_SHARE_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "..", "hooks", "useStatsShare.ts"),
	"utf8",
);

/**
 * Replace string / template-literal / comment contents with spaces, leaving
 * brace structure outside those contexts intact. Line numbers are preserved
 * (newlines are never replaced).
 *
 * Approximations (acceptable for the source file under test):
 *   - Template literals are matched as a whole (including any `${...}`
 *     interpolations). This means braces INSIDE `${...}` are stripped —
 *     which is what we want, since they're part of the template syntax
 *     and shouldn't affect outer brace counting.
 *   - Regex literals are not stripped, but the source under test contains
 *     none with brace characters.
 */
function stripNonCode(src: string): string {
	let result = src;
	// Strip block comments — preserve newlines so line numbers stay aligned.
	result = result.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
	// Strip line comments.
	result = result.replace(/\/\/[^\n]*/g, "");
	// Strip double-quoted strings (handle escaped chars).
	result = result.replace(
		/"(?:[^"\\]|\\.)*"/g,
		(m) => `"${" ".repeat(Math.max(0, m.length - 2))}"`,
	);
	// Strip single-quoted strings.
	result = result.replace(
		/'(?:[^'\\]|\\.)*'/g,
		(m) => `'${" ".repeat(Math.max(0, m.length - 2))}'`,
	);
	// Strip template literals (including their ${...} interpolations).
	result = result.replace(
		/`(?:[^`\\]|\\.)*`/g,
		(m) => `\`${" ".repeat(Math.max(0, m.length - 2))}\``,
	);
	return result;
}

interface ConsoleInfoSite {
	line: number;
	gated: boolean;
}

/**
 * Walk the (stripped) source and, for every `console.info(` token, record
 * whether it sits inside at least one `if (import.meta.env.DEV) { ... }`
 * block.
 */
function analyzeConsoleInfoGating(src: string): ConsoleInfoSite[] {
	const stripped = stripNonCode(src);
	const results: ConsoleInfoSite[] = [];
	let depth = 0;
	// Each frame: { openDepth: number, isDev: boolean }
	const frames: Array<{ openDepth: number; isDev: boolean }> = [];
	let lineNum = 1;

	for (let i = 0; i < stripped.length; i += 1) {
		const ch = stripped[i];

		if (ch === "\n") {
			lineNum += 1;
			continue;
		}

		// Detect `console.info(` token.
		if (ch === "c" && stripped.startsWith("console.info(", i)) {
			const gated = frames.some((f) => f.isDev);
			results.push({ line: lineNum, gated });
			// Skip past the matched token so we don't double-count.
			i += "console.info(".length - 1;
			continue;
		}

		// Detect `if (import.meta.env.DEV)` opener.
		// Match the keyword `if` followed by `(import.meta.env.DEV)`.
		// We look for the next `{` after this point and treat it as the
		// block opener (the `{` may be on the same line).
		if (ch === "i" && stripped.startsWith("if", i)) {
			// Verify it's the keyword `if` (preceded by non-identifier char
			// or start of file, followed by whitespace/paren).
			const prevChar = stripped[i - 1] ?? "";
			const isIdentChar = /[A-Za-z0-9_$]/.test(prevChar);
			const nextCharAfterIf = stripped[i + 2] ?? "";
			const looksLikeIfKeyword =
				!isIdentChar &&
				(nextCharAfterIf === " " ||
					nextCharAfterIf === "(" ||
					nextCharAfterIf === "\t");
			if (looksLikeIfKeyword) {
				// Look ahead (within the same statement) for `import.meta.env.DEV`.
				const window = stripped.slice(i, i + 80);
				const devMatch = /\(\s*import\.meta\.env\.DEV\s*\)/.test(window);
				if (devMatch) {
					// Find the next `{` after the `if (...)` — that's the block opener.
					// Walk forward, skipping the parenthesized condition.
					let j = i + 2;
					let parenDepth = 0;
					while (j < stripped.length) {
						const c = stripped[j];
						if (c === "(") parenDepth += 1;
						else if (c === ")") {
							parenDepth -= 1;
							if (parenDepth === 0) {
								// Found the closing `)` of the condition.
								// Find the next `{` after this.
								let k = j + 1;
								while (
									k < stripped.length &&
									stripped[k] !== "{" &&
									stripped[k] !== ";"
								) {
									k += 1;
								}
								if (k < stripped.length && stripped[k] === "{") {
									// Push a DEV frame at the CURRENT depth (before the `{` is counted).
									frames.push({ openDepth: depth, isDev: true });
									// Don't increment depth here — the `{` will be processed
									// in the next iteration and increment depth normally.
									// But we need to skip past the `{` so we don't push another
									// (non-DEV) frame for it. Set a flag.
									// Actually, simpler: just advance i to k-1 and let the main
									// loop process the `{` (which increments depth but doesn't
									// push a frame because we only push frames for `if` headers).
									i = k - 1;
								}
								break;
							}
						} else if (c === "{" || c === ";") {
							// Malformed — bail.
							break;
						}
						j += 1;
					}
					continue;
				}
			}
		}

		// Track braces.
		if (ch === "{") {
			depth += 1;
		} else if (ch === "}") {
			depth -= 1;
			// Pop any frame whose openDepth >= current depth.
			// (The frame was opened at a shallower-or-equal depth, and the
			// closing `}` brings us back to that depth or below.)
			while (frames.length > 0) {
				const top = frames[frames.length - 1];
				if (top === undefined || top.openDepth < depth) {
					break;
				}
				frames.pop();
			}
		}
	}

	return results;
}

describe("useStatsShare.ts: every console.info must be DEV-gated", () => {
	it("useStatsShare.ts contains at least one console.info call (sanity)", () => {
		// If this assertion fails, the file was refactored to remove all
		// console.info calls — in which case this regression test becomes
		// moot and should be deleted (the invariant it pins is vacuously
		// satisfied). Failing here forces the author to notice.
		const count = (USE_STATS_SHARE_SRC.match(/console\.info\s*\(/g) || [])
			.length;
		expect(count).toBeGreaterThan(0);
	});

	it("every console.info call is inside an `if (import.meta.env.DEV)` block", () => {
		const analysis = analyzeConsoleInfoGating(USE_STATS_SHARE_SRC);
		// Sanity: parser found at least one console.info.
		expect(analysis.length).toBeGreaterThan(0);
		const ungated = analysis.filter((entry) => !entry.gated);
		if (ungated.length > 0) {
			throw new Error(
				`found ${ungated.length} ungated console.info call(s) ` +
					`in useStatsShare.ts at line(s): ${ungated.map((u) => u.line).join(", ")}. ` +
					`Every console.info must be wrapped in \`if (import.meta.env.DEV) { ... }\` ` +
					`so user-data shape (offsetWidth / dimensions / dataUrl prefix) is not leaked ` +
					`to the renderer DevTools console of the packaged app.`,
			);
		}
		expect(ungated).toEqual([]);
	});

	it("no `console.log` calls exist in useStatsShare.ts (debug artifacts)", () => {
		// While the dead debug-only spec that motivated this file has been
		// deleted, we additionally pin that the production hook itself
		// contains no `console.log` calls — `console.log` is never
		// appropriate in production renderer code (use `console.info` +
		// DEV gate for diagnostics, or `console.warn`/`console.error` for
		// actionable messages that should reach the packaged-app console).
		const matches = USE_STATS_SHARE_SRC.match(/console\.log\s*\(/g) || [];
		expect(matches).toEqual([]);
	});
});

describe("this regression file stays free of debug artifacts", () => {
	it("this test file contains no `console.log` calls (debug artifacts)", () => {
		// Pin that this regression test doesn't itself regress to the
		// debug-only form (console.log calls + vacuous assertions that the
		// original dead debug-only spec had). We strip comments
		// and string literals before checking, so mentioning `console.log`
		// in a doc comment or test name doesn't trigger a false positive.
		const thisSrc = fs.readFileSync(__filename, "utf8");
		const stripped = thisSrc
			.replace(/\/\*[\s\S]*?\*\//g, "")
			.replace(/\/\/[^\n]*/g, "")
			.replace(/"(?:[^"\\]|\\.)*"/g, '""')
			.replace(/'(?:[^'\\]|\\.)*'/g, "''")
			.replace(/`(?:[^`\\]|\\.)*`/g, "``");
		const matches = stripped.match(/console\.log\s*\(/g) || [];
		expect(matches).toEqual([]);
	});
});
