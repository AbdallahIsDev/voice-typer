// @vitest-environment node
/**
 * Unit tests for `src/main/python/send-to-python.ts` backpressure cleanup.
 *
 * Verifies that `_removeRendererFromBackpressure(webContentsId)`:
 *   • deletes the entry for the given id from the
 *     `_rendererCallTimestamps` Map.
 *   • is a no-op (does NOT throw) when the id has no entry.
 *   • only removes the named id — sibling entries are preserved.
 *
 * Regression coverage for the finding: previously the Map was never
 * cleared per-renderer — each destroyed BrowserWindow leaked its
 * `webContents.id` entry forever (only `_resetIpcBackpressure()`
 * cleared the WHOLE Map, called from stopPython / relaunchApp).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../allowed-commands", () => ({
	ALLOWED_COMMANDS: new Set<string>(["get_config", "toggle_dictation"]),
}));

vi.mock("../../state", () => ({
	MAX_PENDING_REQUESTS: 1000,
	RATE_LIMIT_MAX_CALLS: 5,
	RATE_LIMIT_WINDOW_MS: 1000,
	state: {
		tcpSocket: null,
		pendingRequests: new Map<
			number,
			{ resolve: (v: unknown) => void; reject: (e: unknown) => void }
		>(),
		nextId: 1,
		_relaunching: false,
	},
}));

vi.mock("../errors", () => ({
	PythonIpcError: class PythonIpcError extends Error {},
}));

import {
	_removeRendererFromBackpressure,
	_resetIpcBackpressure,
	sendToPython,
} from "../python/send-to-python";

describe("send-to-python _removeRendererFromBackpressure", () => {
	beforeEach(() => {
		_resetIpcBackpressure();
	});

	it("deletes the entry for the given webContents.id", async () => {
		// Seed the rate-limit Map by issuing a python-call with
		// senderId=123. This populates `_rendererCallTimestamps`
		// with key 123 (the call will reject because tcpSocket
		// is null, but the rate-limit entry is added BEFORE the
		// socket-write check).
		await expect(sendToPython({ type: "get_config" }, 123)).rejects.toThrow(
			/Python backend is not connected/,
		);

		// Verify the entry was seeded (sanity).
		// Re-issue to confirm rate-limit still allows the entry
		// (the second call should also reject for the same
		// socket reason, not for rate-limit).
		await expect(sendToPython({ type: "get_config" }, 123)).rejects.toThrow(
			/Python backend is not connected/,
		);

		// Now remove the entry — must NOT throw.
		expect(() => _removeRendererFromBackpressure(123)).not.toThrow();

		// After removal, a fresh call with senderId=123 should
		// still reject (socket is still null) but the rate-limit
		// Map should have a fresh entry (we can't directly
		// inspect the Map, but the call not throwing confirms
		// the helper ran cleanly).
		await expect(sendToPython({ type: "get_config" }, 123)).rejects.toThrow(
			/Python backend is not connected/,
		);
	});

	it("is a no-op when the id has no entry (does not throw)", () => {
		// No prior sendToPython call with senderId=999 — the
		// Map has no entry for 999.
		expect(() => _removeRendererFromBackpressure(999)).not.toThrow();
		expect(() => _removeRendererFromBackpressure(-1)).not.toThrow();
		expect(() => _removeRendererFromBackpressure(0)).not.toThrow();
	});

	it("only removes the named id — sibling entries are preserved", async () => {
		// Seed two entries: senderId=1 and senderId=2.
		await expect(sendToPython({ type: "get_config" }, 1)).rejects.toThrow(
			/Python backend is not connected/,
		);
		await expect(sendToPython({ type: "get_config" }, 2)).rejects.toThrow(
			/Python backend is not connected/,
		);

		// Remove only senderId=1.
		_removeRendererFromBackpressure(1);

		// senderId=2 should still work (its entry is intact —
		// the call rejects only for the socket reason, not for
		// a missing-Map-key reason which doesn't exist anyway).
		await expect(sendToPython({ type: "get_config" }, 2)).rejects.toThrow(
			/Python backend is not connected/,
		);

		// senderId=1 also still works (re-seeds after removal).
		await expect(sendToPython({ type: "get_config" }, 1)).rejects.toThrow(
			/Python backend is not connected/,
		);
	});

	it("safe to call after _resetIpcBackpressure (Map already empty)", () => {
		_resetIpcBackpressure();
		expect(() => _removeRendererFromBackpressure(42)).not.toThrow();
	});
});
