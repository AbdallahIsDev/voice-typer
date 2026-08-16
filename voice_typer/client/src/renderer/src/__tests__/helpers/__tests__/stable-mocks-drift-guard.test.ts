/**
 * Drift guard for the shared `helpers/stableMocks.tsx` test harness.
 *
 * Background: every page test used to carry a near-identical preamble —
 * a `vi.hoisted` singleton block (`mockCall`, `mockPythonEvent`,
 * `showSnack`, …) plus the same seven `vi.mock` registrations. The
 * helper now owns both halves, and a file's preamble collapses to one
 * import + a destructure + one `vi.mock` line per module. The same
 * preamble existed in the components and hooks test suites (settings
 * sections, the `useConnection` variants) — they are covered by this
 * guard too.
 *
 * This guard makes the pattern self-enforcing, mirroring
 * `hugeicons-mock-guard.test.ts`:
 *
 *   1. No PAGE / COMPONENT test file (anything under
 *      `src/renderer/src/pages/**` or `.../components/**` in a
 *      `__tests__` dir) — and no HOOKS test file whose harness is the
 *      standard singleton preamble (see the carve-outs below) — may
 *      hand-roll a `vi.hoisted` singleton block, i.e. a `vi.hoisted`
 *      destructure (or plain binding) that declares one of the
 *      standard singleton names (`mockCall`, `mockPythonEvent`,
 *      `showSnack`, …). Such a file must import `stableMocks` from
 *      the helper instead, so the assertable singletons stay the SAME
 *      objects across resets and re-imports (`vi.resetModules()`
 *      re-binds the page module, not the test file's imports) and so
 *      the per-file boilerplate can't drift in mock shape.
 *
 *      Carve-outs (exempt, they own a different pattern):
 *      - hook-level tests under `pages/history/hooks` and
 *        `pages/microphone/hooks` (own callMock / event-registry
 *        patterns, not the page preamble);
 *      - tests under `hooks/models` — the model-hook unit tests use a
 *        `callMock` / sub-hook-mock harness, and the
 *        `useModelLifecycle` facade test mocks all five sub-hooks and
 *        asserts referential pass-through of bespoke vi.fn refs (not
 *        singleton identity across resets);
 *      - hoisted blocks that don't declare a standard singleton name
 *        (e.g. a hoisted `matchMedia` stub, a file-local `mockT` i18n
 *        spy).
 *
 *   2. No stableMocks-based file may statically import a module it
 *      mocks via a stableMocks factory ABOVE its stableMocks import.
 *      The `vi.mock` registrations are hoisted, but the mocked
 *      module's factory BODY runs when that module is first imported —
 *      i.e. at the importing statement's position. If `import { toast
 *      } from "sonner"` (say) appears BEFORE `import { sonnerMock }
 *      from "@/__tests__/helpers/stableMocks"` in source order, the
 *      factory body evaluates while the helper's binding is still in
 *      the TDZ (`Cannot access 'sonnerMock' before initialization`).
 *      An import sorter (biome) reordering a mocked-module import
 *      above the helper import would silently reintroduce that
 *      mock-factory TDZ crash — this test fails first. Type-only
 *      imports are exempt (erased at compile time, no module
 *      evaluation).
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const RENDERER_SRC = resolve(__dirname, "..", "..", ".."); // .../src/renderer/src
const PAGES_DIR = join(RENDERER_SRC, "pages");
const COMPONENTS_DIR = join(RENDERER_SRC, "components");
const HOOKS_DIR = join(RENDERER_SRC, "hooks");

/** The singleton names owned by `helpers/stableMocks.tsx`. */
const SINGLETON_NAMES = [
	"mockCall",
	"mockPythonEvent",
	"showSnack",
	"mockShowSnack",
	"mockNavigate",
	"markUpdated",
	"mockToastError",
	"toastSuccess",
	"toastError",
	"toastWarning",
	"toastInfo",
	"toastDismiss",
	"mockPendingConsentField",
	"mockConsumeConsentField",
];

/**
 * A hand-rolled singleton block — two shapes:
 *  1. destructure: `const { mockCall, mockPythonEvent } = vi.hoisted(…)`
 *     The body char class is `[^{}]` (newlines allowed, braces not) —
 *     a destructure binding list never contains a nested brace, so the
 *     match closes at the FIRST `}` and can never span across other
 *     statements (a `[^\s\S]` lazy span would run from one
 *     `const { … }` through intervening code into a later `= vi.hoisted`
 *     and fabricate offenders);
 *  2. plain binding: `const mockCall = vi.hoisted(() => vi.fn())`.
 */
const SINGLETON_HOIST_DESTRUCTURE_RE =
	/const\s*\{([^{}]*)\}\s*=\s*vi\.hoisted/g;
const SINGLETON_HOIST_PLAIN_RE = new RegExp(
	`const\\s+(${SINGLETON_NAMES.join("|")})\\s*=\\s*vi\\.hoisted`,
	"g",
);

/** True when the destructure body binds one of the standard names.
 *  `\b` guards keep `showSnack` from matching inside `mockShowSnack`
 *  and `toastError` inside `mockToastError` (longer alternants first
 *  anyway). */
function declaresSingleton(body: string): boolean {
	return new RegExp(`\\b(${SINGLETON_NAMES.join("|")})\\b`).test(body);
}

/** Remove /* block comments and // line comments so doc examples can't
 *  produce false positives. Newline-preserving: block-comment bytes
 *  become spaces (comment NEWLINES stay), so `lineOf()` indices match
 *  the original file's line numbers after stripping. */
function stripComments(src: string): string {
	const noBlocks = src.replace(/\/\*[\s\S]*?\*\//g, (m) =>
		m.replace(/[^\n]/g, " "),
	);
	return noBlocks.replace(/(^|[^:])[ \t]*\/\/.*$/gm, "$1");
}

/** Index of the start of `src`, counting newlines before `offset`. */
function lineOf(offset: number, src: string): number {
	let line = 1;
	for (let i = 0; i < offset && i < src.length; i++) {
		if (src[i] === "\n") line++;
	}
	return line;
}

function walkFiles(dir: string, out: string[] = []): string[] {
	if (!existsSync(dir)) return out;
	for (const entry of readdirSync(dir)) {
		const full = join(dir, entry);
		const st = statSync(full);
		if (st.isDirectory()) {
			if (entry === "node_modules" || entry === "dist" || entry === "out") {
				continue;
			}
			walkFiles(full, out);
		} else if (
			st.isFile() &&
			(full.endsWith(".test.ts") || full.endsWith(".test.tsx"))
		) {
			out.push(full);
		}
	}
	return out;
}

/** Carve-outs: directories whose tests own a different mock pattern
 *  (see the file header). */
function isCarveOut(file: string): boolean {
	return (
		file.includes(`${join("pages", "history", "hooks")}`) ||
		file.includes(`${join("pages", "microphone", "hooks")}`) ||
		file.startsWith(join(HOOKS_DIR, "models"))
	);
}

describe("stableMocks page-test drift guard", () => {
	it("no page/component/hooks test file hand-rolls a vi.hoisted singleton block (must import stableMocks)", () => {
		const offenders: Array<{ path: string; line: number }> = [];
		for (const root of [PAGES_DIR, COMPONENTS_DIR, HOOKS_DIR]) {
			for (const file of walkFiles(root)) {
				if (isCarveOut(file)) continue;
				const src = stripComments(readFileSync(file, "utf8"));
				for (const match of src.matchAll(SINGLETON_HOIST_DESTRUCTURE_RE)) {
					if (declaresSingleton(match[1] ?? "")) {
						offenders.push({
							path: file.replace(RENDERER_SRC, "<renderer-src>"),
							line: lineOf(match.index ?? 0, src),
						});
					}
				}
				for (const match of src.matchAll(SINGLETON_HOIST_PLAIN_RE)) {
					offenders.push({
						path: file.replace(RENDERER_SRC, "<renderer-src>"),
						line: lineOf(match.index ?? 0, src),
					});
				}
			}
		}
		expect(
			offenders,
			`[guard] hand-rolled vi.hoisted singleton blocks found in ` +
				`page/component/hook tests — import stableMocks + the shape ` +
				`factories from @/__tests__/helpers/stableMocks (one ` +
				`vi.mock line per module) instead. Offenders:\n` +
				offenders.map((o) => `  - ${o.path}:${o.line}`).join("\n"),
		).toEqual([]);
	});
});

// ── Guard 2: stableMocks import order (mock-factory TDZ) ─────────────

/** The canonical module specifiers that stableMocks' shape factories
 *  mock. A static VALUE import of one of these in a stableMocks-based
 *  file triggers that module's vi.mock factory body at the import's
 *  position — the factory closes over the helper's bindings, so the
 *  helper import must come first. */
const STABLE_MOCK_FACTORY_MODULES = [
	"@/hooks/usePython",
	"@/hooks/useSnackbar",
	"@/hooks/useNavigation",
	"@/hooks/useLastUpdated",
	"@hugeicons/react",
	"@hugeicons/core-free-icons",
	"sonner",
	"next-themes",
] as const;

/** The stableMocks import statement. The clause char class excludes
 *  quotes and semicolons, so the match must start at the import
 *  statement itself — it can never begin at an earlier `import` and
 *  span across other statements into the helper's specifier. */
const STABLE_MOCKS_IMPORT_RE =
	/import\s+[^"';]*?from\s+["']@\/__tests__\/helpers\/stableMocks["']/;

/** A static VALUE import statement — exact grammar so the match can't
 *  span into other statements. `import type` and inline `type` clauses
 *  are excluded (erased at compile time — no module evaluation, so no
 *  factory trigger). */
const VALUE_IMPORT_RE =
	/(?<![\w$])import\s+(?:type\s+(?:[\w$]+\s*,\s*)?(?:\{[^}]*\}|\*\s+as\s+[\w$]+|[\w$]+)\s*from\s*)?(?:[\w$]+\s*,\s*)?(?:\{[^}]*\}|\*\s+as\s+[\w$]+|[\w$]+)\s*from\s*["']([^"']+)["']/g;

describe("stableMocks import-order guard (mock-factory TDZ)", () => {
	it("no stableMocks-based test file statically imports a mocked module above the stableMocks import", () => {
		const offenders: Array<{
			path: string;
			line: number;
			mod: string;
			helperLine: number;
		}> = [];
		for (const root of [PAGES_DIR, COMPONENTS_DIR, HOOKS_DIR]) {
			for (const file of walkFiles(root)) {
				const raw = readFileSync(file, "utf8");
				const src = stripComments(raw);
				// BOTH positions come from the SAME stripped source
				// (stripComments is newline-preserving), so lineOf()
				// indices are comparable; matching on `raw` would
				// count the helper import's offset in a longer text
				// and report a later line than the import-order scan
				// sees.
				const helperMatch = src.match(STABLE_MOCKS_IMPORT_RE);
				if (!helperMatch) continue;
				const helperLine = lineOf(helperMatch.index ?? 0, src);
				for (const match of src.matchAll(VALUE_IMPORT_RE)) {
					const mod = match[1];
					if (
						!(STABLE_MOCK_FACTORY_MODULES as readonly string[]).includes(
							mod ?? "",
						)
					) {
						continue;
					}
					const importLine = lineOf(match.index ?? 0, src);
					if (importLine < helperLine) {
						offenders.push({
							path: file.replace(RENDERER_SRC, "<renderer-src>"),
							line: importLine,
							mod: mod ?? "",
							helperLine,
						});
					}
				}
			}
		}
		expect(
			offenders,
			`[guard] a stableMocks-based test file statically imports a ` +
				`mocked module ABOVE its stableMocks import — the mocked ` +
				`module's vi.mock factory body would run while the helper's ` +
				`binding is still in the TDZ. Move the ` +
				`@/__tests__/helpers/stableMocks import above every static ` +
				`import of a module the file mocks (or assert via the ` +
				`stableMocks singletons instead of importing the mocked ` +
				`module). Offenders:\n` +
				offenders
					.map(
						(o) =>
							`  - ${o.path}:${o.line} imports "${o.mod}" above the ` +
							`stableMocks import (line ${o.helperLine})`,
					)
					.join("\n"),
		).toEqual([]);
	});
});
