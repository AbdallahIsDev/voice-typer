/**
 * @vitest-environment node
 *
 *  regression test: `main.tsx` (and `bubble-main.tsx`) MUST
 * call `installGlobalErrorHandlers()` BEFORE `ReactDOM.createRoot().render()`.
 *
 * The global error / `unhandledrejection` handlers in
 * `globalErrorHandler.ts` are the only safety net for async errors
 * that escape React's `ErrorBoundary` (e.g. unhandled promise
 * rejections in `useEffect`, top-level `await` failures in dynamically
 * imported modules). Without the install call, these errors are
 * SILENTLY swallowed — the exact regression the module was written to
 * prevent. `ErrorBoundary` only catches render-phase errors; it
 * cannot catch async ones.
 *
 * This test string-matches the source so the regression cannot recur
 * even if someone refactors the import statement or accidentally
 * removes the call. Reading the source (rather than importing the
 * module) is intentional: importing `main.tsx` would execute the
 * render pipeline and side-effects (React mount, `window.bubble`
 * access, etc.) which we don't want in a unit test.
 *
 * The test runs in a `node` environment (no jsdom) because it only
 * does `fs.readFileSync` + string assertions — no DOM access needed.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const MAIN_TSX = readFileSync(resolve(__dirname, "../../main.tsx"), "utf-8");

const BUBBLE_MAIN_TSX = readFileSync(
	resolve(__dirname, "../../bubble-main.tsx"),
	"utf-8",
);

describe("PVT-G5-016: installGlobalErrorHandlers is wired into the renderer entrypoints", () => {
	it("main.tsx imports installGlobalErrorHandlers from @/lib/globalErrorHandler", () => {
		expect(MAIN_TSX).toContain("installGlobalErrorHandlers");
		expect(MAIN_TSX).toMatch(
			/import\s+\{\s*installGlobalErrorHandlers\s*\}\s+from\s+["']@\/lib\/globalErrorHandler["']/,
		);
	});

	it("main.tsx calls installGlobalErrorHandlers() BEFORE ReactDOM.createRoot(...).render(...)", () => {
		// Find the install call (with optional whitespace/semicolon).
		const installMatch = MAIN_TSX.match(
			/installGlobalErrorHandlers\s*\(\s*\)\s*;?/,
		);
		expect(installMatch).not.toBeNull();
		const installIdx = installMatch?.index ?? -1;

		// Match the actual render call (`ReactDOM.createRoot(<el>).render(`),
		// NOT a comment that mentions `ReactDOM.createRoot` (e.g. the
		// explanatory comment we added above the install call).
		const renderMatch = MAIN_TSX.match(
			/ReactDOM\.createRoot\([^)]+\)\.render\(/,
		);
		expect(renderMatch).not.toBeNull();
		const renderIdx = renderMatch?.index ?? -1;

		expect(installIdx).toBeGreaterThan(-1);
		expect(renderIdx).toBeGreaterThan(-1);
		// Strict ordering: install MUST come before render.
		expect(installIdx).toBeLessThan(renderIdx);
	});

	it("bubble-main.tsx imports installGlobalErrorHandlers from ./lib/globalErrorHandler", () => {
		expect(BUBBLE_MAIN_TSX).toContain("installGlobalErrorHandlers");
		expect(BUBBLE_MAIN_TSX).toMatch(
			/import\s+\{\s*installGlobalErrorHandlers\s*\}\s+from\s+["']\.\/lib\/globalErrorHandler["']/,
		);
	});

	it("bubble-main.tsx calls installGlobalErrorHandlers() BEFORE ReactDOM.createRoot(...).render(...)", () => {
		const installMatch = BUBBLE_MAIN_TSX.match(
			/installGlobalErrorHandlers\s*\(\s*\)\s*;?/,
		);
		expect(installMatch).not.toBeNull();
		const installIdx = installMatch?.index ?? -1;

		// Match the actual render call (see main.tsx test for rationale).
		const renderMatch = BUBBLE_MAIN_TSX.match(
			/ReactDOM\.createRoot\([^)]+\)\.render\(/,
		);
		expect(renderMatch).not.toBeNull();
		const renderIdx = renderMatch?.index ?? -1;

		expect(installIdx).toBeGreaterThan(-1);
		expect(renderIdx).toBeGreaterThan(-1);
		expect(installIdx).toBeLessThan(renderIdx);
	});
});
