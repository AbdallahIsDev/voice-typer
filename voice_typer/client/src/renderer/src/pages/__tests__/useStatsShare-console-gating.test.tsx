import { describe, expect, it } from "vitest";

const fs = require("node:fs");
const path = require("node:path");

const USE_STATS_SHARE_SRC = fs.readFileSync(
	path.resolve(__dirname, "..", "..", "hooks", "useStatsShare.ts"),
	"utf8",
);

function stripNonCode(src: string): string {
	let result = src;
	// Strip block comments, preserve newlines so line numbers stay aligned.
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
					// Find the next `{` after the `if (...)`, that's the block opener.
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
									// Don't increment depth here, the `{` will be processed
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
							// Malformed, bail.
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
		// contains no `console.log` calls, `console.log` is never
		// appropriate in production renderer code (use `console.info` +
		// DEV gate for diagnostics, or `console.warn`/`console.error` for
		// actionable messages that should reach the packaged-app console).
		const matches = USE_STATS_SHARE_SRC.match(/console\.log\s*\(/g) || [];
		expect(matches).toEqual([]);
	});
});
