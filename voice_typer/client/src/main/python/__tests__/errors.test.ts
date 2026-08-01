// @vitest-environment node
/**
 * Regression test for the typed `PythonIpcError` class.
 *
 * Pre-fix, `sendToPython` rejected its returned Promise with a bare
 * `new Error(string)` for 5 of 6 failure cases (only the timeout site
 * set `err.code = "timeout"`). The `python-call` IPC bridge therefore
 * classified timeouts via a fragile `/timeout/i` regex on the
 * human-readable message string — which would silently break if the
 * message wording ever changed.
 *
 * Post-fix, every reject site in `sendToPython` constructs a
 * `PythonIpcError(code, message)` and the bridge branches on
 * `err instanceof PythonIpcError` + `err.code` directly. These tests
 * pin the typed contract so a future refactor that reverts to bare
 * `Error` (or removes the `code` field) fails loudly.
 */
import { describe, expect, it } from "vitest";

import type { PythonCallErrorCode } from "../../ipc/python-call-handler";
import { PythonIpcError } from "../errors";

describe("PythonIpcError", () => {
	it("sets `.code` and `.message` from constructor args", () => {
		const code: PythonCallErrorCode = "command_timeout";
		const err = new PythonIpcError(
			code,
			"Timeout after 15s for command: heartbeat",
		);
		expect(err.code).toBe("command_timeout");
		expect(err.message).toBe("Timeout after 15s for command: heartbeat");
	});

	it("preserves `.code` for every PythonCallErrorCode value", () => {
		const cases: PythonCallErrorCode[] = [
			"backend_not_connected",
			"backend_exited_early",
			"command_failed",
			"command_timeout",
		];
		for (const code of cases) {
			const err = new PythonIpcError(code, `msg-${code}`);
			expect(err.code).toBe(code);
			expect(err.message).toBe(`msg-${code}`);
		}
	});

	it("is an instance of Error", () => {
		const err = new PythonIpcError("command_failed", "boom");
		expect(err).toBeInstanceOf(Error);
	});

	it("is an instance of PythonIpcError", () => {
		const err = new PythonIpcError("command_failed", "boom");
		expect(err).toBeInstanceOf(PythonIpcError);
	});

	it('sets `.name` to "PythonIpcError"', () => {
		const err = new PythonIpcError("backend_not_connected", "offline");
		expect(err.name).toBe("PythonIpcError");
	});

	it("a bare Error is NOT an instance of PythonIpcError", () => {
		// Defense-in-depth: the `python-call-handler` bridge falls back
		// to `"command_failed"` for any non-PythonIpcError rejection.
		// This test pins that contract — a plain `Error` must NOT be
		// misclassified as a `PythonIpcError` (which would read an
		// undefined `.code` and produce a typed-code field of type
		// `undefined`).
		const bare = new Error("Timeout after 15s");
		expect(bare).not.toBeInstanceOf(PythonIpcError);
	});
});
