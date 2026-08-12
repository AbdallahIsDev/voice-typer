/**
 * XZ-R16-09 regression guard: every `console.*` call in renderer
 * PRODUCTION code must prefix its first string argument with
 * `[renderer:<module>]`.
 *
 * The renderer forwards console output to the Electron main-process log
 * via `webContents.on("console-message")`, so a consistent prefix is what
 * lets operators grep a log line back to the emitting module. This scan
 * prevents the convention from silently regressing (mixed bare
 * `[Module]` tags or unprefixed messages were the original finding).
 *
 * The scanner is comment-aware: `console.*` mentions inside `//` line
 * comments and slash-star ... star-slash block comments (which often
 * document the expected log shape) are ignored.
 */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const RENDERER_SRC = path.resolve(__dirname, "../../");
const CALL_RE = /\bconsole\.(?:warn|error|log|debug|info)\(/;
const PREFIX_RE = /^\[renderer:[^\]/]+(?:\/[^\]/]+)?\]/;

/** Walk a directory recursively, returning file paths. */
function walk(dir: string): string[] {
	const out: string[] = [];
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			if (entry.name === "__tests__" || entry.name === "node_modules") continue;
			out.push(...walk(full));
		} else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) {
			out.push(full);
		}
	}
	return out;
}

/**
 * Strip `//` line comments and block comments (slash-star ... star-slash) from
 * a line,
 * tracking block-comment state across lines and respecting string
 * literals (so `http://...` or `"/*"` inside strings are preserved).
 * Returns the code-only text and the updated block-comment state.
 */
function stripComments(
	line: string,
	inBlock: boolean,
): { code: string; inBlock: boolean } {
	let out = "";
	let i = 0;
	let inString: "'" | '"' | "`" | null = null;
	while (i < line.length) {
		const c = line[i];
		const next = line[i + 1] ?? "";
		if (inBlock) {
			if (c === "*" && next === "/") {
				inBlock = false;
				i += 2;
			} else {
				i += 1;
			}
			continue;
		}
		if (inString) {
			out += c;
			if (c === "\\") {
				out += next;
				i += 2;
				continue;
			}
			if (c === inString) inString = null;
			i += 1;
			continue;
		}
		if (c === "'" || c === '"' || c === "`") {
			inString = c;
			out += c;
			i += 1;
			continue;
		}
		if (c === "/" && next === "/") {
			// line comment — drop the rest
			break;
		}
		if (c === "/" && next === "*") {
			inBlock = true;
			i += 2;
			continue;
		}
		out += c;
		i += 1;
	}
	return { code: out, inBlock };
}

/**
 * Given a line containing a `console.X(` call, gather the full call text
 * (with continuations) starting from the call's own opening paren.
 * Returns the first string-literal argument's content (or null if the
 * first argument is not a string literal) plus the full call span.
 */
function firstStringArg(
	lines: string[],
	startLine: number,
	callStart: number,
): { content: string | null; span: string } {
	let buf = "";
	let depth = 0;
	let j = startLine;
	let col = callStart;
	let done = false;
	while (!done && j < lines.length) {
		const line = lines[j] ?? "";
		while (col < line.length) {
			const c = line[col];
			buf += c;
			if (c === "(") depth += 1;
			else if (c === ")") {
				depth -= 1;
				if (depth === 0) {
					done = true;
					break;
				}
			}
			col += 1;
		}
		if (!done) {
			j += 1;
			col = 0;
		}
	}
	const trimmed = buf.trimStart();
	if (
		trimmed.startsWith("'") ||
		trimmed.startsWith('"') ||
		trimmed.startsWith("`")
	) {
		const quote = trimmed[0];
		let content = "";
		let k = 1;
		while (k < trimmed.length) {
			const c = trimmed[k];
			if (c === "\\") {
				content += trimmed[k + 1] ?? "";
				k += 2;
				continue;
			}
			if (c === quote) break;
			content += c;
			k += 1;
		}
		return { content, span: buf };
	}
	return { content: null, span: buf };
}

describe("renderer log prefix convention (XZ-R16-09)", () => {
	it("every console call in production code carries a [renderer:...] first-arg prefix", () => {
		const violations: string[] = [];
		for (const file of walk(RENDERER_SRC)) {
			const rawLines = readFileSync(file, "utf-8").split(/\r?\n/);
			let inBlock = false;
			// code-only lines (comments stripped, line count preserved via nulls)
			const codeLines: Array<string | null> = rawLines.map((raw) => {
				const { code, inBlock: newInBlock } = stripComments(raw, inBlock);
				inBlock = newInBlock;
				return code.trim().length > 0 ? code : null;
			});
			for (let i = 0; i < codeLines.length; i++) {
				const line = codeLines[i];
				if (!line) continue;
				let searchFrom = 0;
				let match = CALL_RE.exec(line.slice(searchFrom));
				while (match) {
					const callStart = searchFrom + match.index + match[0].length;
					// Rebuild a full-line array for the continuation walk so
					// comment stripping is consistent across the call span.
					const fullLines: string[] = [];
					let blk = false;
					for (const raw of rawLines) {
						const r = stripComments(raw, blk);
						blk = r.inBlock;
						fullLines.push(r.code);
					}
					const { content, span } = firstStringArg(fullLines, i, callStart);
					const rel = path.relative(RENDERER_SRC, file);
					if (content === null) {
						// Non-string first arg — allow if a [renderer:...] tag appears
						// anywhere within the full call span (multi-line aware).
						if (!/\[renderer:/.test(span)) {
							violations.push(`${rel}:${i + 1}: non-string first arg`);
						}
					} else if (!PREFIX_RE.test(content)) {
						violations.push(`${rel}:${i + 1}: ${content.slice(0, 80)}`);
					}
					searchFrom = callStart;
					match = CALL_RE.exec(line.slice(searchFrom));
				}
			}
		}
		expect(violations).toEqual([]);
	});
});
