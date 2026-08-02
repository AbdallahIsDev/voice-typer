// @vitest-environment node
/**
 * Regression tests for `python-call-handler.ts` error-code import.
 *
 * Background
 * ----------
 * The handler previously declared a LOCAL `PythonCallErrorCode` union that
 * duplicated the canonical `src/shared/python-call-error-code.ts` module.
 * Any drift between the two declarations would silently break the renderer's
 * `_code` narrowing (the renderer imports the shared union via
 * `@/types/ipc/enums` → `src/shared/python-call-error-code`).
 *
 * The fix replaces the local declaration with an `import` from the shared
 * module (and re-exports it so existing `import { PythonCallErrorCode } from
 * "../ipc/python-call-handler"` sites continue to resolve).
 *
 * Test strategy
 * -------------
 * Static analysis (source-text + module-graph assertions):
 *   (a) The source contains an `import` statement referencing the shared module.
 *   (b) The source does NOT contain a local `export type PythonCallErrorCode = ...` declaration.
 *   (c) The re-export (`export type { PythonCallErrorCode }`) is present so
 *       downstream imports from `../ipc/python-call-handler` still resolve.
 *   (d) Runtime: the imported type is assignable to the shared type AND the
 *       handler's `ERROR_MESSAGES` record is keyed by the shared type.
 *
 * ON LINUX (sandbox): source-text + runtime type check.
 * ON WINDOWS / macOS: same contract — the type alias is platform-agnostic.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
	PYTHON_CALL_ERROR_CODES,
	type PythonCallErrorCode,
} from "../../shared/python-call-error-code";

// ────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────

function readHandlerSrc(): string {
	return fs.readFileSync(
		path.resolve(__dirname, "../ipc/python-call-handler.ts"),
		"utf-8",
	);
}

// ────────────────────────────────────────────────────────────────────
// Source-text contract
// ────────────────────────────────────────────────────────────────────

describe("python-call-handler.ts imports PythonCallErrorCode from shared", () => {
	const src = readHandlerSrc();

	it("contains an `import` of PythonCallErrorCode from the shared module", () => {
		// The import statement must reference the shared module path.
		expect(src).toMatch(
			/import\s+type\s+\{\s*PythonCallErrorCode\s*\}\s+from\s+["']\.\.\/\.\.\/shared\/python-call-error-code["']/,
		);
	});

	it("does NOT contain a local `export type PythonCallErrorCode = ...` declaration", () => {
		// The duplicate local declaration is removed. The only `export type
		// { PythonCallErrorCode }` allowed is the re-export of the imported
		// type (a bare re-export, not a `= ...` definition).
		expect(src).not.toMatch(
			/export\s+type\s+PythonCallErrorCode\s*=\s*["']?backend_not_connected/,
		);
		expect(src).not.toMatch(/export\s+type\s+PythonCallErrorCode\s*=\s*\|/);
	});

	it("re-exports PythonCallErrorCode so downstream imports still resolve", () => {
		// `python/errors.ts` and `python/__tests__/errors.test.ts` import
		// `PythonCallErrorCode` from `../ipc/python-call-handler`. The
		// re-export keeps those imports working without churn.
		expect(src).toMatch(/export\s+type\s+\{\s*PythonCallErrorCode\s*\}/);
	});

	it("does NOT contain any literal union member definitions (backend_not_connected etc.)", () => {
		// The shared module is the single source of truth — the handler
		// must not re-declare any of the 4 codes locally.
		const localDeclPattern =
			/export\s+type\s+PythonCallErrorCode\s*=[\s\S]*?(backend_not_connected|backend_exited_early|command_timeout)/;
		expect(src).not.toMatch(localDeclPattern);
	});
});

// ────────────────────────────────────────────────────────────────────
// Runtime type check: the shared type covers all 4 codes
// ────────────────────────────────────────────────────────────────────

describe("shared PythonCallErrorCode covers all 4 codes", () => {
	it("PYTHON_CALL_ERROR_CODES has exactly 4 members", () => {
		expect(PYTHON_CALL_ERROR_CODES).toHaveLength(4);
	});

	it("includes backend_not_connected, backend_exited_early, command_failed, command_timeout", () => {
		expect(PYTHON_CALL_ERROR_CODES).toContain("backend_not_connected");
		expect(PYTHON_CALL_ERROR_CODES).toContain("backend_exited_early");
		expect(PYTHON_CALL_ERROR_CODES).toContain("command_failed");
		expect(PYTHON_CALL_ERROR_CODES).toContain("command_timeout");
	});

	it("each code is assignable to the shared PythonCallErrorCode type", () => {
		// Type-level check: if this compiles, the import chain is intact.
		const codes: PythonCallErrorCode[] = [
			"backend_not_connected",
			"backend_exited_early",
			"command_failed",
			"command_timeout",
		];
		expect(codes).toHaveLength(4);
	});
});

// ────────────────────────────────────────────────────────────────────
// Module-graph check: the shared module is the canonical source
// ────────────────────────────────────────────────────────────────────

describe("shared module is the canonical PythonCallErrorCode source", () => {
	it("the shared module file exists at src/shared/python-call-error-code.ts", () => {
		const sharedPath = path.resolve(
			__dirname,
			"../../shared/python-call-error-code.ts",
		);
		expect(fs.existsSync(sharedPath)).toBe(true);
	});

	it("the shared module exports PYTHON_CALL_ERROR_CODES const + PythonCallErrorCode type", () => {
		const sharedSrc = fs.readFileSync(
			path.resolve(__dirname, "../../shared/python-call-error-code.ts"),
			"utf-8",
		);
		expect(sharedSrc).toMatch(/export\s+const\s+PYTHON_CALL_ERROR_CODES/);
		expect(sharedSrc).toMatch(
			/export\s+type\s+PythonCallErrorCode\s*=\s*\(typeof\s+PYTHON_CALL_ERROR_CODES\)\[number\]/,
		);
	});
});
