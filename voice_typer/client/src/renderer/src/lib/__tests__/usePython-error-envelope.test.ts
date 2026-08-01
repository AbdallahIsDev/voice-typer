/**
 * Regression tests for the  / d-review  fix in
 * `usePython.ts`.
 *
 * The gap: the Electron `python-call` IPC handler
 * (`client/src/main/index.ts:1904-1918`) can resolve the pending
 * request with EITHER of two error-envelope shapes:
 *
 *   1. `{_error: "..."}` (string) — Electron main-process synthetic
 *      errors (backend-not-connected, sendToPython exceptions).
 *   2. `{type:"error", data:{code, message}}` — Python server
 *      unhandled-dispatch exceptions (`server/ipc_server.py:1044-1050`),
 *      passed through verbatim.
 *
 * Previously `usePython` only checked `"_error" in result`, so the
 * `type:"error"` envelope was silently treated as a successful result
 * and callers downstream read `undefined` from data fields. The fix
 * adds a `type:"error"` branch alongside the existing `_error` branch
 * and throws a real `Error` (with the message extracted from either
 * envelope) so `try { await python.call(...) } catch (e) {}` callers
 * see failures on both shapes.
 *
 * On Tauri these in-code checks are dead code (the Rust `dispatch`
 * command rejects the `invoke` promise on `type:"error"` before the
 * resolved value reaches JS), but the same `usePython.ts` bundle ships
 * under both hosts — these tests cover the Electron-path logic that
 * the in-code guards implement.
 */
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { usePython } from "@/hooks/usePython";

// Shape of the `window.python` namespace the hook reads. We install a
// minimal mock on `window.python` per test and tear it down after.
interface PythonBridgeMock {
	call: ReturnType<typeof vi.fn>;
	onEvent: ReturnType<typeof vi.fn>;
}

describe("usePython — NEW-IPC-107 error-envelope handling", () => {
	let original: PythonBridgeMock | undefined;

	beforeEach(() => {
		original = (window as unknown as { python?: PythonBridgeMock }).python;
	});

	afterEach(() => {
		const w = window as unknown as { python?: PythonBridgeMock };
		if (original === undefined) {
			delete w.python;
		} else {
			w.python = original;
		}
		cleanup();
		vi.restoreAllMocks();
	});

	function installPythonMock(callImpl: () => Promise<unknown>) {
		const mock: PythonBridgeMock = {
			call: vi.fn(callImpl),
			onEvent: vi.fn(() => () => {}),
		};
		(window as unknown as { python: PythonBridgeMock }).python = mock;
		return mock;
	}

	it("throws an Error with the server message on `{type:'error', data:{message}}` envelope", async () => {
		installPythonMock(() =>
			Promise.resolve({ type: "error", data: { message: "boom" } }),
		);

		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).rejects.toThrow("boom");
	});

	it("throws an Error with the server message when the error envelope also carries a `code`", async () => {
		// This is the exact shape ipc_server.py:1044-1050 produces on
		// unhandled dispatch exceptions.
		installPythonMock(() =>
			Promise.resolve({
				type: "error",
				data: { code: "internal_error", message: "internal error" },
			}),
		);

		const { result } = renderHook(() => usePython());

		// The unified error shape surfaces just the raw message (no
		// "server error [code]:" prefix) so callers can match on the
		// human-readable text the server emitted.
		await expect(result.current.call("get_config")).rejects.toThrow(
			"internal error",
		);
	});

	it("throws an Error with the `_error` message on `{_error:{message}}` envelope (object form)", async () => {
		// Defensive: the actual Electron code sends `_error` as a string,
		// but we accept the object form too so a future refactor of
		// index.ts:1908/1911/1916 (e.g. switching to `{_error:{message}}`
		// for richer error metadata) doesn't silently break callers.
		installPythonMock(() =>
			Promise.resolve({ _error: { message: "conn lost" } }),
		);

		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).rejects.toThrow(
			"conn lost",
		);
	});

	it("throws an Error with the `_error` string on `{_error:'...'}` envelope (actual Electron shape)", async () => {
		// This is the real shape the Electron main process produces at
		// index.ts:1908/1911/1916: `return { _error: "..." }` (string,
		// not an object). The guard must handle this form correctly.
		installPythonMock(() =>
			Promise.resolve({ _error: "Python backend is not connected" }),
		);

		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).rejects.toThrow(
			"Python backend is not connected",
		);
	});

	it("does NOT throw on a successful result envelope (`{type:'result', data:{...}}`)", async () => {
		installPythonMock(() =>
			Promise.resolve({ type: "result", data: { status: "idle" } }),
		);

		const { result } = renderHook(() => usePython());

		// Should resolve without throwing; the full envelope is returned
		// (callers that want just `data` destructure it themselves, but
		// the contract is "no throw on success").
		const res = await result.current.call("get_status");
		expect(res).toEqual({ type: "result", data: { status: "idle" } });
	});

	it("does NOT throw on a bare data object (Tauri success shape, where Rust unwraps `response.data`)", async () => {
		// On Tauri the Rust `dispatch` command returns `response.data`
		// directly (main.rs:967), so `window.python.call` resolves with
		// the bare data object — no `type` field, no envelope. This
		// confirms the guards don't false-positive on the Tauri success
		// shape.
		installPythonMock(() => Promise.resolve({ status: "idle" }));

		const { result } = renderHook(() => usePython());

		const res = await result.current.call("get_status");
		expect(res).toEqual({ status: "idle" });
	});

	it("falls back to 'unknown error' when the `type:'error'` envelope has no `data.message`", async () => {
		installPythonMock(() => Promise.resolve({ type: "error", data: {} }));

		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).rejects.toThrow(
			"unknown error",
		);
	});

	it("throws 'Python bridge not available' when window.python is missing", async () => {
		// No mock installed — window.python is undefined.
		delete (window as unknown as { python?: PythonBridgeMock }).python;

		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).rejects.toThrow(
			"Python bridge not available",
		);
	});
});
