// @vitest-environment node
/**
 * XS-78: behavioral tests for `src/main/python/send-to-python.ts`.
 *
 * Covers the SEC-019 allowlist enforcement + RACE early-reject paths:
 *   - Disallowed commands are rejected BEFORE the socket write
 *     (so a compromised renderer can't invoke arbitrary IPC commands).
 *   - When `state._relaunching === true`, the call rejects immediately
 *     with "Application is restarting" — pending IPC calls don't sit
 *     in `pendingRequests` until the 5s timeout.
 *   - When `state.tcpSocket === null`, the call rejects immediately
 *     with "Python backend is not connected".
 *   - Per-renderer rate limiting: a sender that exceeds
 *     `RATE_LIMIT_MAX_CALLS` in `RATE_LIMIT_WINDOW_MS` is rejected.
 *
 * The actual 120s timeout + socket-write paths require a fake socket
 * + fake timers; the existing `xv-fa19-fixes.test.ts` covers the
 * timer-not-unref'd source contract. This file complements it with
 * the early-reject behavior (the most security-relevant paths).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
	return {
		socketWrite: vi.fn<(data: string) => boolean>(() => true),
	};
});

vi.mock("../allowed-commands", () => ({
	ALLOWED_COMMANDS: new Set<string>([
		"get_config",
		"set_config",
		"toggle_dictation",
		"heartbeat",
		"quit_app",
		"download_model",
		// Add a sentinel "disallowed_for_tests" command that is NOT in
		// the set so we can exercise the rejection path without poking
		// the real ALLOWED_COMMANDS (which would couple this test to
		// every future addition / removal).
	]),
}));

vi.mock("../state", () => ({
	MAX_PENDING_REQUESTS: 1000,
	RATE_LIMIT_MAX_CALLS: 5,
	RATE_LIMIT_WINDOW_MS: 1000,
	state: {
		// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
		tcpSocket: { write: mocks.socketWrite } as any,
		pendingRequests: new Map<
			number,
			{ resolve: (v: unknown) => void; reject: (e: unknown) => void }
		>(),
		nextId: 1,
		_relaunching: false,
	},
}));

import { _resetIpcBackpressure, sendToPython } from "../python/send-to-python";
import { state } from "../state";

describe("XS-78: send-to-python.ts", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.pendingRequests.clear();
		state.nextId = 1;
		state._relaunching = false;
		// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
		state.tcpSocket = { write: mocks.socketWrite } as any;
		_resetIpcBackpressure();
	});

	describe("SEC-019: ALLOWED_COMMANDS enforcement", () => {
		it("rejects a command NOT in ALLOWED_COMMANDS BEFORE writing to the socket", async () => {
			await expect(
				sendToPython({ type: "disallowed_for_tests" }),
			).rejects.toThrow(/Disallowed IPC command: disallowed_for_tests/);

			// Crucially, the socket must NOT have been written — the
			// allowlist gate runs before `state.tcpSocket.write`.
			expect(mocks.socketWrite).not.toHaveBeenCalled();
			// And no entry must be left in pendingRequests (the gate
			// runs before the entry is added).
			expect(state.pendingRequests.size).toBe(0);
		});

		it("writes the JSON line + id to the socket for an allowlisted command", async () => {
			// Don't await — the promise won't resolve until handleMessage
			// routes the reply. We just want to assert the side effects.
			void sendToPython({ type: "get_config" });

			expect(mocks.socketWrite).toHaveBeenCalledTimes(1);
			const line = mocks.socketWrite.mock.calls[0]?.[0] ?? "";
			expect(line.endsWith("\n")).toBe(true);
			const parsed = JSON.parse(line);
			expect(parsed.type).toBe("get_config");
			expect(typeof parsed.id).toBe("number");
			// pendingRequests entry was added with the same id.
			expect(state.pendingRequests.has(parsed.id)).toBe(true);
		});
	});

	describe("RACE: state._relaunching early reject", () => {
		it("rejects with 'Application is restarting' when _relaunching is true", async () => {
			state._relaunching = true;
			await expect(sendToPython({ type: "get_config" })).rejects.toThrow(
				/Application is restarting/,
			);

			// No socket write, no pendingRequests entry.
			expect(mocks.socketWrite).not.toHaveBeenCalled();
			expect(state.pendingRequests.size).toBe(0);
		});

		it("does NOT check _relaunching AFTER the allowlist (order matters for security)", async () => {
			// A disallowed command during relaunch must still be rejected
			// with the *disallowed* error (not the relaunching error),
			// because the allowlist check is the security boundary and
			// must run first. (If the relaunching check ran first, a
			// compromised renderer could probe for the relaunching
			// state by sending disallowed commands and observing the
			// error message.)
			state._relaunching = true;
			await expect(
				sendToPython({ type: "disallowed_for_tests" }),
			).rejects.toThrow(/Disallowed IPC command/);
		});
	});

	describe("not-connected early reject", () => {
		it("rejects with 'Python backend is not connected' when tcpSocket is null", async () => {
			state.tcpSocket = null;
			await expect(sendToPython({ type: "get_config" })).rejects.toThrow(
				/Python backend is not connected/,
			);

			expect(mocks.socketWrite).not.toHaveBeenCalled();
		});
	});

	describe("per-renderer rate limiting", () => {
		it("rejects the 6th call from the same sender within the window", async () => {
			// RATE_LIMIT_MAX_CALLS = 5 in the mock. The first 5 should
			// go through (write to socket); the 6th should be rejected
			// with a rate-limit error.
			const senderId = 12345;
			for (let i = 0; i < 5; i++) {
				void sendToPython({ type: "get_config" }, senderId);
			}
			expect(mocks.socketWrite).toHaveBeenCalledTimes(5);

			await expect(
				sendToPython({ type: "get_config" }, senderId),
			).rejects.toThrow(/Rate limit exceeded/);
			// The 6th call must not have written to the socket.
			expect(mocks.socketWrite).toHaveBeenCalledTimes(5);
		});

		it("does NOT rate-limit main-process callers (senderId === null)", async () => {
			// Main-process internal callers skip the per-renderer limit
			// (they're trusted and the global MAX_PENDING_REQUESTS cap
			// still applies).
			for (let i = 0; i < 10; i++) {
				void sendToPython({ type: "get_config" }, null);
			}
			expect(mocks.socketWrite).toHaveBeenCalledTimes(10);
		});

		it("rate limit is per-sender (two senders each get their own budget)", async () => {
			// Sender A makes 5 calls (uses up its budget).
			for (let i = 0; i < 5; i++) {
				void sendToPython({ type: "get_config" }, 1);
			}
			// Sender B should still be able to make calls.
			void sendToPython({ type: "get_config" }, 2);
			expect(mocks.socketWrite).toHaveBeenCalledTimes(6);
		});
	});

	describe("_resetIpcBackpressure() (TY-35)", () => {
		it("clears the per-renderer rate-limit state so a freshly-booted backend gets a clean slate", async () => {
			const senderId = 99;
			// Exhaust the budget.
			for (let i = 0; i < 5; i++) {
				void sendToPython({ type: "get_config" }, senderId);
			}
			// Confirm we're rate-limited.
			await expect(
				sendToPython({ type: "get_config" }, senderId),
			).rejects.toThrow(/Rate limit exceeded/);

			// Reset.
			_resetIpcBackpressure();

			// The same sender should now be able to make calls again.
			void sendToPython({ type: "get_config" }, senderId);
			expect(mocks.socketWrite).toHaveBeenCalledTimes(6);
		});
	});
});
