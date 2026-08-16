/**
 * Source-pattern guard for the OOM render-loop class of bug.
 *
 * The infinite-loop class (FATAL worker heap OOM during the axe-core
 * scans): a `useEffect` that lists `usePython()`'s `call` in its
 * dependency array re-fires whenever the `call` identity changes.
 * `call` is `useCallback(..., [])`-stable in production, but a test
 * mock (or future code) that hands out a FRESH `call` on every render
 * re-triggers such an effect — fetch → setState → render → new `call`
 * → effect → … until the heap is exhausted.
 *
 * The sanctioned fix (established in `Home.tsx`, `useHistoryCache.ts`,
 * `useVocabulary.ts`, … 15 files): mirror `call` into a ref
 *
 *     const callRef = useRef(call);
 *     useEffect(() => {
 *         callRef.current = call;
 *     }, [call]);
 *
 * and read `callRef.current` inside the effect, keeping the effect's
 * deps free of `call`. The mirror effect itself depends on `call` BY
 * DESIGN — it only writes a ref and never triggers state updates, so
 * it cannot loop.
 *
 * This test statically scans every renderer source file (production
 * and tests) and fails when a `useEffect` / `useLayoutEffect`
 * dependency array contains `call` in any form, EXCEPT the exact
 * callRef-mirror idiom above. A review-time `call`-in-deps addition
 * (or a regression that reverts a callRef migration) fails here with
 * the file + line, instead of OOMing a worker at runtime.
 *
 * Second rule (`useCallback` / `useMemo` chains): a `useCallback` that
 * lists `call` in deps and is CONSUMED by a non-mirror `useEffect` /
 * `useLayoutEffect` / `useMemo` dep array re-runs that consumer's work
 * on every `call` identity change (a test mock handing out a fresh
 * `call` per render re-fires the effect / recomputes the memo every
 * render — the same loop class one hop away). Recreating a callback
 * for event handlers / JSX props is NOT flagged (no implicit
 * re-execution); only effect/memo consumers re-run work.
 *
 * The rule: for every `useCallback` with `call` in deps, fail when its
 * name appears in a non-mirror effect/memo dep array in a file that
 * (a) is the def site, or (b) neither defines its own `useCallback`
 * with that name nor imports the name from another module (a
 * prop-passed callback, e.g. App → useConnectionToasts). Consumer
 * effects that are themselves ref-mirrors (`xRef.current = X`) are
 * exempt — they write a ref and cannot loop (the sanctioned
 * absorption mechanism).
 *
 * Uses a real AST (`@babel/parser`, a vite dependency) — a regex scan
 * would miss the multiline / destructured forms this guard is meant to
 * catch.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { parse } from "@babel/parser";
import type { Node } from "@babel/types";
import { describe, expect, it } from "vitest";

/** Parse a source file with the TS/JSX plugins the scan needs. Older
 *  @babel/parser docs suggested an `isTSX` option; 7.29 removed it and
 *  the typescript plugin auto-detects JSX-in-TS (the option was already
 *  a silent no-op when this test ran green). */
const parseTs = (src: string) =>
	parse(src, {
		sourceType: "module",
		plugins: [["typescript", {}], "jsx"],
		errorRecovery: false,
	});

/** Narrow an unknown value to a babel AST node. The generic child walk
 *  (`Object.keys(node)`) yields fields that are numbers (`start`/`end`)
 *  or Comment objects, so the runtime `type` check needs an explicit
 *  guard rather than a bare `as Node` cast. */
const asNode = (v: unknown): v is Node =>
	typeof v === "object" &&
	v !== null &&
	typeof (v as { type?: unknown }).type === "string";

// The client's `typescript` dependency is the TS 7 native build whose
// JS entry only exports version info, so the AST here comes from
// `@babel/parser` (a vite dependency, always present).

const RENDERER_SRC = resolve(__dirname, "..");
const EFFECT_HOOKS = new Set(["useEffect", "useLayoutEffect"]);

function walk(dir: string, out: string[] = []): string[] {
	for (const entry of readdirSync(dir)) {
		const p = join(dir, entry);
		if (statSync(p).isDirectory()) {
			walk(p, out);
		} else if (/\.(ts|tsx)$/.test(entry)) {
			out.push(p);
		}
	}
	return out;
}

/** True when `node` is the callRef-mirror idiom:
 * `callRef.current = call` (a single-statement effect body). */
function isCallRefMirrorBody(
	body: Node | undefined,
	deps: readonly (Node | null)[],
): boolean {
	if (body?.type !== "BlockStatement" || body.body.length !== 1) {
		return false;
	}
	const stmt = body.body[0];
	if (stmt?.type !== "ExpressionStatement") return false;
	const expr = stmt.expression;
	if (expr.type !== "AssignmentExpression" || expr.operator !== "=") {
		return false;
	}
	const left = expr.left;
	if (left.type !== "MemberExpression" || left.property.type !== "Identifier") {
		return false;
	}
	if (left.property.name !== "current" || left.object.type !== "Identifier") {
		return false;
	}
	if (!left.object.name.endsWith("Ref")) return false;
	// Right side must be the `call` identifier, and the deps array must
	// be exactly `[call]`.
	if (expr.right.type !== "Identifier" || expr.right.name !== "call") {
		return false;
	}
	return (
		deps.length === 1 &&
		deps[0]?.type === "Identifier" &&
		deps[0].name === "call"
	);
}

/** True when the dep element references `call` (direct identifier or
 * `.call` member access). */
function referencesCall(el: Node | null): boolean {
	if (!el) return false;
	if (el.type === "Identifier") return el.name === "call";
	if (el.type === "MemberExpression" && el.property.type === "Identifier") {
		return el.property.name === "call";
	}
	return false;
}

function findViolations(file: string, src: string): string[] {
	const ast = parseTs(src);
	const violations: string[] = [];
	const visit = (node: Node): void => {
		if (
			node.type === "CallExpression" &&
			node.callee.type === "Identifier" &&
			EFFECT_HOOKS.has(node.callee.name) &&
			node.arguments.length >= 2
		) {
			const deps = node.arguments[1];
			if (deps && deps.type === "ArrayExpression") {
				const hasCall = deps.elements.some(referencesCall);
				const effectBody = (node.arguments[0] as { body?: Node } | null)?.body;
				if (hasCall && !isCallRefMirrorBody(effectBody, deps.elements)) {
					const line = node.loc?.start.line ?? 0;
					violations.push(`${file.replace(`${RENDERER_SRC}/`, "")}:${line}`);
				}
			}
		}
		for (const key of Object.keys(node) as (keyof Node)[]) {
			const child = node[key];
			if (Array.isArray(child)) {
				for (const item of child) {
					if (asNode(item)) visit(item);
				}
			} else if (asNode(child)) {
				visit(child);
			}
		}
	};
	visit(ast.program);
	return violations;
}

describe("useEffect deps must not contain usePython's `call` (OOM loop class)", () => {
	it("no effect dependency array contains `call` except the callRef-mirror idiom", () => {
		const files = walk(RENDERER_SRC);
		expect(files.length).toBeGreaterThan(100);

		const violations: string[] = [];
		for (const file of files) {
			const src = readFileSync(file, "utf8");
			violations.push(...findViolations(file, src));
		}

		expect(violations).toEqual([]);
		if (violations.length > 0) {
			// The failure message points straight at the offending effect.
			expect(violations.join("\n")).toBe("");
		}
	});
});

// ── Rule 2: useCallback-with-call feeding a non-mirror effect/memo ────

const MEMO_OR_EFFECT_HOOKS = new Set([
	"useEffect",
	"useLayoutEffect",
	"useMemo",
]);

/** True when an effect body is a single `xRef.current = <id>` assignment
 * (the sanctioned ref-mirror idiom — writes a ref, cannot loop). */
function isRefMirrorEffectBody(body: Node | undefined): boolean {
	if (body?.type !== "BlockStatement" || body.body.length !== 1) {
		return false;
	}
	const stmt = body.body[0];
	if (stmt?.type !== "ExpressionStatement") return false;
	const expr = stmt.expression;
	if (expr.type !== "AssignmentExpression" || expr.operator !== "=") {
		return false;
	}
	const left = expr.left;
	if (left.type !== "MemberExpression" || left.property.type !== "Identifier") {
		return false;
	}
	return (
		left.property.name === "current" &&
		left.object.type === "Identifier" &&
		left.object.name.endsWith("Ref") &&
		expr.right.type === "Identifier"
	);
}

/** Resolve an import source (`@/...`, relative) to a path relative to
 * `RENDERER_SRC`, or null when it isn't a local module. */
function resolveImportSource(source: string, fromFile: string): string | null {
	let base: string;
	if (source.startsWith("@/")) {
		// `@/` resolves to `RENDERER_SRC` itself (the vite alias), so
		// the module path is just the suffix after `@/`.
		base = source.slice(2);
	} else if (source.startsWith(".")) {
		base = relative(RENDERER_SRC, resolve(dirname(fromFile), source)).replace(
			/\\/g,
			"/",
		);
	} else {
		return null; // bare package import
	}
	const candidates = [
		base,
		`${base}.ts`,
		`${base}.tsx`,
		`${base}/index.ts`,
		`${base}/index.tsx`,
	];
	for (const candidate of candidates) {
		if (candidate.endsWith(".ts") || candidate.endsWith(".tsx")) {
			if (existsSync(join(RENDERER_SRC, candidate))) return candidate;
		}
	}
	return null;
}

interface CallbackFeedViolation {
	def: string;
	consumer: string;
}

/** Find `useCallback` sites with `call` in deps whose name feeds a
 * non-mirror effect/memo dep array (cross-file, import-aware). */
function findCallbackFeedViolations(): CallbackFeedViolation[] {
	const files = walk(RENDERER_SRC);

	type FileInfo = {
		flagged: { name: string; start: number; line: number }[];
		localCallbacks: Set<string>;
		imported: Map<string, string>; // local name → resolved source
		// name → resolved source of the hook that produced it (e.g.
		// `const { updateConfig } = useSettingsConfig()`).
		hookProvided: Map<string, string>;
		consumers: { name: string; hook: string; line: number }[];
	};
	const infos = new Map<string, FileInfo>();

	for (const file of files) {
		const src = readFileSync(file, "utf8");
		const ast = parseTs(src);
		const info: FileInfo = {
			flagged: [],
			localCallbacks: new Set(),
			imported: new Map(),
			hookProvided: new Map(),
			consumers: [],
		};
		infos.set(file, info);

		const visit = (node: Node): void => {
			if (node.type === "CallExpression" && node.callee.type === "Identifier") {
				const hook = node.callee.name;
				const line = node.loc?.start.line ?? 0;
				if (hook === "useCallback" && node.arguments.length >= 2) {
					const deps = node.arguments[1];
					if (deps?.type !== "ArrayExpression") return;
					const hasCall = deps.elements.some(referencesCall);
					if (hasCall) {
						info.flagged.push({ name: "?", start: node.start ?? 0, line });
					}
				}
				if (MEMO_OR_EFFECT_HOOKS.has(hook) && node.arguments.length >= 2) {
					const deps = node.arguments[1];
					if (deps?.type !== "ArrayExpression") return;
					const isMirror =
						(hook === "useEffect" || hook === "useLayoutEffect") &&
						isRefMirrorEffectBody(
							(node.arguments[0] as { body?: Node } | null)?.body,
						);
					if (isMirror) return;
					for (const el of deps.elements) {
						if (el?.type === "Identifier") {
							info.consumers.push({ name: el.name, hook, line });
						}
					}
				}
			}
			for (const key of Object.keys(node) as (keyof Node)[]) {
				const child = node[key];
				if (Array.isArray(child)) {
					for (const item of child) {
						if (asNode(item)) visit(item);
					}
				} else if (asNode(child)) {
					visit(child);
				}
			}
		};
		visit(ast.program);
	}

	// Second pass: fill in the flagged callback NAMES by matching each
	// useCallback call site to its enclosing VariableDeclarator init.
	for (const file of files) {
		const info = infos.get(file);
		if (!info) continue;
		const src = readFileSync(file, "utf8");
		const ast = parseTs(src);
		// Map VariableDeclarator.init.start → name, and collect imports +
		// local useCallback names + hook-destructured names (for the
		// cross-file exemption logic).
		const initNames = new Map<number, string>();
		// Hook-call destructures (`const { X } = useFoo()` / `const X =
		// useFoo()`) collected raw, then resolved against the imports
		// map AFTER the visit (imports are declared before use).
		const hookDestructures: { name: string; fnName: string }[] = [];
		const visit = (node: Node): void => {
			if (node.type === "VariableDeclarator" && node.init) {
				if (typeof node.init.start === "number") {
					const id = node.id;
					if (id.type === "Identifier") initNames.set(node.init.start, id.name);
				}
				if (node.init.type === "CallExpression") {
					const callee = node.init.callee;
					if (callee.type === "Identifier" && callee.name.startsWith("use")) {
						if (node.id.type === "ObjectPattern") {
							for (const prop of node.id.properties) {
								if (
									prop.type === "ObjectProperty" &&
									prop.value.type === "Identifier"
								) {
									hookDestructures.push({
										name: prop.value.name,
										fnName: callee.name,
									});
								}
							}
						} else if (node.id.type === "Identifier") {
							hookDestructures.push({
								name: node.id.name,
								fnName: callee.name,
							});
						}
					}
				}
			}
			if (node.type === "ImportDeclaration") {
				const source = node.source.value;
				for (const spec of node.specifiers) {
					if (
						spec.type === "ImportSpecifier" &&
						spec.local.type === "Identifier"
					) {
						const resolved = resolveImportSource(source, file);
						if (resolved) info.imported.set(spec.local.name, resolved);
					}
					if (
						spec.type === "ImportDefaultSpecifier" &&
						spec.local.type === "Identifier"
					) {
						const resolved = resolveImportSource(source, file);
						if (resolved) info.imported.set(spec.local.name, resolved);
					}
				}
			}
			for (const key of Object.keys(node) as (keyof Node)[]) {
				const child = node[key];
				if (Array.isArray(child)) {
					for (const item of child) {
						if (asNode(item)) visit(item);
					}
				} else if (asNode(child)) {
					visit(child);
				}
			}
		};
		visit(ast.program);

		// Resolve hook destructures against the imports collected in
		// this file (a hook must be imported — `useX` called bare is a
		// local/global, leave un-attributed).
		for (const { name, fnName } of hookDestructures) {
			const source = info.imported.get(fnName);
			if (source) info.hookProvided.set(name, source);
		}

		// Resolve flagged names (matched by the useCallback call-site
		// start offset, NOT the line number — VariableDeclarator.init
		// keys use byte offsets).
		info.flagged = info.flagged.map((f) => ({
			name: initNames.get(f.start) ?? "",
			start: f.start,
			line: f.line,
		}));
		info.flagged = info.flagged.filter((f) => f.name !== "");
		for (const name of info.flagged.map((f) => f.name)) {
			info.localCallbacks.add(name);
		}
	}

	// Third pass: match flagged defs against consumers.
	const violations: CallbackFeedViolation[] = [];
	const fileLabel = (p: string) =>
		relative(RENDERER_SRC, p).replace(/\\/g, "/");
	for (const [file, info] of infos) {
		for (const flagged of info.flagged) {
			for (const [consumerFile, consumerInfo] of infos) {
				if (consumerFile === file) continue; // handled below
				// Exempt: consumer defines its own useCallback with the
				// name (the reference resolves locally), or the name comes
				// from an import / hook destructure of a module OTHER than
				// the def file (the reference is a different definition).
				if (consumerInfo.localCallbacks.has(flagged.name)) continue;
				const importedFrom = consumerInfo.imported.get(flagged.name);
				if (importedFrom !== undefined && importedFrom !== fileLabel(file)) {
					continue;
				}
				const hookFrom = consumerInfo.hookProvided.get(flagged.name);
				if (hookFrom !== undefined && hookFrom !== fileLabel(file)) {
					continue;
				}
				for (const consumer of consumerInfo.consumers) {
					if (consumer.name !== flagged.name) continue;
					violations.push({
						def: `${fileLabel(file)}:${flagged.line}`,
						consumer: `${fileLabel(consumerFile)}:${consumer.line} (${consumer.hook})`,
					});
				}
			}
			// Same-file consumers.
			for (const consumer of info.consumers) {
				if (consumer.name !== flagged.name) continue;
				violations.push({
					def: `${fileLabel(file)}:${flagged.line}`,
					consumer: `${fileLabel(file)}:${consumer.line} (${consumer.hook})`,
				});
			}
		}
	}
	return violations;
}

describe("useCallback with `call` in deps must not feed effects/memos (OOM loop class)", () => {
	it("no call-in-deps callback is consumed by a non-mirror effect/memo dep array", () => {
		const files = walk(RENDERER_SRC);
		expect(files.length).toBeGreaterThan(100);

		const violations = findCallbackFeedViolations();
		expect(violations).toEqual([]);
		if (violations.length > 0) {
			expect(
				violations
					.map(
						(v) => `${v.def} (callback with call in deps) feeds ${v.consumer}`,
					)
					.join("\n"),
			).toBe("");
		}
	});
});
