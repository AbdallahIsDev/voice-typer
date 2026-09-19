import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", "..", ".."); // .../src/renderer/src
const CLIENT_DIR = resolve(RENDERER_SRC, "..", "..", ".."); // .../client
// predecessor main/preload trees were deleted with the predecessor shell;
// keep only roots that still exist so the guard does not ENOENT.
const MAIN_SRC = join(CLIENT_DIR, "src", "main");
const PRELOAD_SRC = join(CLIENT_DIR, "src", "preload");
const ROOTS = [
	RENDERER_SRC,
	...(existsSync(MAIN_SRC) ? [MAIN_SRC] : []),
	...(existsSync(PRELOAD_SRC) ? [PRELOAD_SRC] : []),
];

/** `vi.mock("module-path"` / `vi.doMock("module-path"` (double or single
 *  quotes; the path may be followed by a comma / parens / factory). */
const VI_MOCK_RE = /vi\.mock\(\s*["']([^"']+)["']/g;
const DO_MOCK_RE = /vi\.doMock\(\s*["']([^"']+)["']/g;

/** Remove block + line comments so docstring examples can't produce
 *  false positives (same helper as the hugeicons mock drift guard). */
function stripComments(src: string): string {
	const noBlocks = src.replace(/\/\*[\s\S]*?\*\//g, " ");
	// Only treat `//` as a comment when NOT part of a `://` URL.
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

function mockPaths(src: string, re: RegExp): Set<string> {
	const paths = new Set<string>();
	for (const match of src.matchAll(re)) {
		const p = match[1];
		if (p) paths.add(p);
	}
	return paths;
}

/** Count registrations per path (a path registered >1× is a runtime
 *  re-registration, the same flake class as a vi.doMock override). */
function countMockPaths(src: string, re: RegExp): Map<string, number> {
	const counts = new Map<string, number>();
	for (const match of src.matchAll(re)) {
		const p = match[1];
		if (p) counts.set(p, (counts.get(p) ?? 0) + 1);
	}
	return counts;
}

describe("vi.doMock drift guard (hoisted vi.mock + doMock same-path overlap)", () => {
	it("no file registers both a hoisted vi.mock(X) and vi.doMock(X) for the same module path", () => {
		// Self-validating path resolution: if the __dirname math above
		// drifts (file moved / vitest changes its module-dir shim),
		// fail loudly with the resolved paths instead of silently
		// scanning the wrong tree (a too-narrow scan turns this guard
		// .../client/src/src/main).
		expect(
			existsSync(join(RENDERER_SRC, "App.tsx")),
			`[guard] RENDERER_SRC misresolved: ${RENDERER_SRC}`,
		).toBe(true);
		expect(
			existsSync(join(CLIENT_DIR, "package.json")),
			`[guard] CLIENT_DIR misresolved: ${CLIENT_DIR}`,
		).toBe(true);

		const offenders: Array<{ path: string; risky: string[] }> = [];
		for (const root of ROOTS) {
			for (const file of walkFiles(root)) {
				const src = stripComments(readFileSync(file, "utf8"));
				const hoisted = mockPaths(src, VI_MOCK_RE);
				const domocked = mockPaths(src, DO_MOCK_RE);
				const overlap = [...hoisted].filter((p) => domocked.has(p));
				// A path registered with vi.mock(X) more than once is the
				// same risk class as the doMock overlap: the second
				// registration is a runtime re-registration (vi.mock
				// called inside it()/beforeEach ≡ vi.doMock) and can be
				// dropped the same way under full-suite load.
				const duplicated = [...countMockPaths(src, VI_MOCK_RE)]
					.filter(([, n]) => n > 1)
					.map(([p]) => p);
				const risky = [...new Set([...overlap, ...duplicated])];
				if (risky.length > 0) {
					offenders.push({
						path: file,
						risky: risky.sort(),
					});
				}
			}
		}
		// Second arg = assertion message: it IS displayed when the
		// assertion fails, so the fix path surfaces instead of dying in
		// an unreachable console.error.
		expect(
			offenders,
			`[guard] hoisted vi.mock + per-test vi.doMock overlap, or a ` +
				`module path registered with vi.mock(X) more than once, ` +
				`found. Both are order-dependent re-registrations that can ` +
				`be dropped under full-suite load (dynamic import resolving ` +
				`before the late factory applies). Convert the per-test ` +
				`override to a hoisted mutable mock: a vi.hoisted vi.fn() ` +
				`delegated to by the hoisted vi.mock factory, default ` +
				`restored in beforeEach, per-test ` +
				`mockReturnValue/mockImplementation (or vi.importActual for ` +
				`the real module). Offenders:\n` +
				offenders.map((o) => `  - ${o.path}: ${o.risky.join(", ")}`).join("\n"),
		).toEqual([]);
	});
});
