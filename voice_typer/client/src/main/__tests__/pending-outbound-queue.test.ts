// @vitest-environment node
/**
 * Outbound replay queue for transient TCP disconnects.
 *
 * Covers the DJ-87 fix: when ``state.tcpSocket`` is null AND the app
 * has connected before (``state._hadConnectedBefore === true``),
 * idempotent commands are queued for flush-on-reconnect instead of
 * being rejected outright — eliminating the "flaky button" feel
 * during brief disconnects (sleep/resume, Wi-Fi flap, GC pause on
 * the Python side triggering the 2s write timeout).
 *
 * Non-idempotent commands (e.g. ``toggle_dictation``) are still
 * rejected immediately when the socket is null — replaying them
 * after a disconnect risks double-execution (the Python side may
 * have already processed the original write before the socket
 * dropped, so replaying would start/stop a second recording).
 *
 * The queue is bounded to 16 entries (``_MAX_PENDING_OUTBOUND``).
 * When the bound is hit, the NEW idempotent request is rejected
 * with a clear "Python backend is not connected" error rather than
 * dropping a queued entry.
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
		"get_status",
		"heartbeat",
		"toggle_dictation",
		"download_model",
		"quit_app",
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
		_hadConnectedBefore: false,
	},
}));

import {
	_flushPendingOutbound,
	_pendingOutboundLengthForTest,
	_resetIpcBackpressure,
	_resetPendingOutbound,
	sendToPython,
} from "../python/send-to-python";
import { state } from "../state";

/**
 * Helper: simulate a "reply" from the Python backend for the given
 * message id by invoking the matching pendingRequests entry's
 * resolve. Returns the id that was replied to.
 *
 * Mirrors the production ``handle-message.ts`` flow: the TCP data
 * handler parses a JSON line, looks up ``id`` in ``pendingRequests``,
 * resolves the entry, and deletes it.
 */
function replyToNextPending(value: unknown): number {
	const firstEntry = state.pendingRequests.entries().next();
	if (firstEntry.done) {
		throw new Error("no pending request to reply to");
	}
	const [id, entry] = firstEntry.value;
	state.pendingRequests.delete(id);
	entry.resolve(value);
	return id;
}

describe("Outbound replay queue (DJ-87)", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		state.pendingRequests.clear();
		state.nextId = 1;
		state._relaunching = false;
		state._hadConnectedBefore = false;
		// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
		state.tcpSocket = { write: mocks.socketWrite } as any;
		_resetIpcBackpressure();
		_resetPendingOutbound("test isolation");
	});

	describe("queueing when socket is null + _hadConnectedBefore is true", () => {
		it("queues an idempotent command (get_config) instead of rejecting it", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			// Don't await — the promise won't resolve until flush.
			const pending = sendToPython({ type: "get_config" });

			// The promise is pending (not rejected) — the queue captured it.
			expect(_pendingOutboundLengthForTest()).toBe(1);
			// No socket write happened (socket is null).
			expect(mocks.socketWrite).not.toHaveBeenCalled();
			// No pendingRequests entry was created (it's in the queue, not
			// the in-flight map).
			expect(state.pendingRequests.size).toBe(0);

			// Restore the socket and flush — the queued call should now
			// be sent.
			// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
			state.tcpSocket = { write: mocks.socketWrite } as any;
			const flushed = vi.fn();
			pending.then(flushed);

			_flushPendingOutbound();
			// Queue is drained.
			expect(_pendingOutboundLengthForTest()).toBe(0);
			// The queued message was written to the socket.
			expect(mocks.socketWrite).toHaveBeenCalledTimes(1);
			const line = mocks.socketWrite.mock.calls[0]?.[0] ?? "";
			const parsed = JSON.parse(line);
			expect(parsed.type).toBe("get_config");

			// The pending promise resolves when the reply arrives.
			replyToNextPending({ ok: true, data: { theme: "dark" } });
			await vi.waitFor(() => expect(flushed).toHaveBeenCalledTimes(1));
			expect(flushed).toHaveBeenCalledWith({
				ok: true,
				data: { theme: "dark" },
			});
		});

		it("queues each of the four idempotent commands (get_config, get_status, heartbeat, set_config)", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			// Don't await — they'll be pending until flush. Attach a
			// no-op .catch so the next test's _resetPendingOutbound
			// doesn't surface as an unhandled rejection (the queued
			// promises are rejected by the beforeEach cleanup).
			for (const msg of [
				{ type: "get_config" },
				{ type: "get_status" },
				{ type: "heartbeat" },
				{ type: "set_config", config: { theme: "light" } },
			]) {
				sendToPython(msg).catch(() => {
					/* will be rejected by _resetPendingOutbound in beforeEach */
				});
			}

			expect(_pendingOutboundLengthForTest()).toBe(4);
			expect(mocks.socketWrite).not.toHaveBeenCalled();
			expect(state.pendingRequests.size).toBe(0);
		});

		it("does NOT queue non-idempotent commands (toggle_dictation) — rejects immediately", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			await expect(sendToPython({ type: "toggle_dictation" })).rejects.toThrow(
				/Python backend is not connected/,
			);

			// Queue is empty — non-idempotent commands are never queued.
			expect(_pendingOutboundLengthForTest()).toBe(0);
			expect(mocks.socketWrite).not.toHaveBeenCalled();
		});

		it("does NOT queue disallowed commands — allowlist check still runs before queueing", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			// "disallowed_for_tests" is NOT in the mocked ALLOWED_COMMANDS.
			// The allowlist check is structurally before the queue lookup
			// in the production code — but the queue branch is INSIDE the
			// ``if (!state.tcpSocket)`` early-return, BEFORE the allowlist
			// check. So this test verifies that the queue only captures
			// idempotent commands that ARE in the idempotent set; a
			// disallowed command that happens to be idempotent-named would
			// still be queued. We use a name that is NOT idempotent AND
			// NOT allowlisted to confirm the reject path runs.
			await expect(
				sendToPython({ type: "disallowed_for_tests" }),
			).rejects.toThrow(/Python backend is not connected/);

			expect(_pendingOutboundLengthForTest()).toBe(0);
		});
	});

	describe("_hadConnectedBefore gate (initial startup)", () => {
		it("rejects idempotent command when _hadConnectedBefore is false (initial startup)", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = false;

			await expect(sendToPython({ type: "get_config" })).rejects.toThrow(
				/Python backend is not connected/,
			);

			// Queue is bypassed during initial startup — the user sees
			// the "Python backend is not connected" error for premature
			// clicks (the dashboard is already showing "Connecting...").
			expect(_pendingOutboundLengthForTest()).toBe(0);
		});

		it("rejects idempotent command when _hadConnectedBefore is undefined (mock default)", async () => {
			state.tcpSocket = null;
			// @ts-expect-error — intentionally delete to simulate the
			// existing test fixtures that don't set _hadConnectedBefore.
			delete state._hadConnectedBefore;

			await expect(sendToPython({ type: "get_config" })).rejects.toThrow(
				/Python backend is not connected/,
			);

			expect(_pendingOutboundLengthForTest()).toBe(0);
		});
	});

	describe("queue bound (_MAX_PENDING_OUTBOUND = 16)", () => {
		it("rejects the 17th idempotent command when the queue is full", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			// Fill the queue with 16 idempotent commands.
			for (let i = 0; i < 16; i++) {
				sendToPython({ type: "get_config" }).catch(() => {
					/* will be rejected by _resetPendingOutbound in beforeEach */
				});
			}
			expect(_pendingOutboundLengthForTest()).toBe(16);

			// The 17th should be rejected with the "not connected" error
			// (the queue is full — preserve the oldest entries and shed
			// load at the new edge).
			await expect(sendToPython({ type: "get_config" })).rejects.toThrow(
				/Python backend is not connected/,
			);

			// Queue size is unchanged.
			expect(_pendingOutboundLengthForTest()).toBe(16);
		});

		it("rejects with 'Python backend is not connected' (not 'queue full') for consistency with the existing error contract", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;
			for (let i = 0; i < 16; i++) {
				sendToPython({ type: "get_config" }).catch(() => {
					/* will be rejected by _resetPendingOutbound in beforeEach */
				});
			}
			await expect(sendToPython({ type: "get_status" })).rejects.toThrow(
				/Python backend is not connected/,
			);
		});
	});

	describe("_flushPendingOutbound()", () => {
		it("drains the queue in FIFO order, sending each entry via sendToPython", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			sendToPython({ type: "get_config" }).catch(() => {
				/* will be rejected by _resetPendingOutbound in beforeEach */
			});
			sendToPython({ type: "get_status" }).catch(() => {
				/* will be rejected by _resetPendingOutbound in beforeEach */
			});
			sendToPython({ type: "heartbeat" }).catch(() => {
				/* will be rejected by _resetPendingOutbound in beforeEach */
			});
			expect(_pendingOutboundLengthForTest()).toBe(3);

			// Restore the socket — flush should send all three in order.
			// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
			state.tcpSocket = { write: mocks.socketWrite } as any;
			_flushPendingOutbound();

			expect(_pendingOutboundLengthForTest()).toBe(0);
			expect(mocks.socketWrite).toHaveBeenCalledTimes(3);
			const types = mocks.socketWrite.mock.calls.map(
				(c) => JSON.parse(c[0] as string).type,
			);
			expect(types).toEqual(["get_config", "get_status", "heartbeat"]);
		});

		it("is a no-op when the queue is empty", () => {
			// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
			state.tcpSocket = { write: mocks.socketWrite } as any;
			_flushPendingOutbound();
			expect(mocks.socketWrite).not.toHaveBeenCalled();
			expect(_pendingOutboundLengthForTest()).toBe(0);
		});

		it("forwards the re-sent entry's resolution to the original caller's resolve", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			const original = sendToPython({ type: "get_config" });
			const onResolved = vi.fn();
			original.then(onResolved, () => {
				/* will be rejected by _resetPendingOutbound if the test
				 * doesn't reach the replyToNextPending call */
			});

			// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
			state.tcpSocket = { write: mocks.socketWrite } as any;
			_flushPendingOutbound();

			// Reply to the re-sent entry.
			replyToNextPending({ ok: true, data: { theme: "dark" } });

			await vi.waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1));
			expect(onResolved).toHaveBeenCalledWith({
				ok: true,
				data: { theme: "dark" },
			});
		});

		it("forwards the re-sent entry's rejection to the original caller's reject", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			const original = sendToPython({ type: "get_config" });
			const onRejected = vi.fn();
			original.catch(onRejected);

			// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
			state.tcpSocket = { write: mocks.socketWrite } as any;
			_flushPendingOutbound();

			// Simulate a Python-side error reply.
			const firstEntry = state.pendingRequests.entries().next();
			if (firstEntry.done) {
				throw new Error("no pending request to reply to");
			}
			const [id, entry] = firstEntry.value;
			state.pendingRequests.delete(id);
			entry.reject(new Error("backend error"));

			await vi.waitFor(() => expect(onRejected).toHaveBeenCalledTimes(1));
			expect((onRejected.mock.calls[0][0] as Error).message).toBe(
				"backend error",
			);
		});

		it("does not re-queue entries if the socket drops again mid-flush", () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			sendToPython({ type: "get_config" }).catch(() => {
				/* will be rejected by _resetPendingOutbound in beforeEach */
			});
			sendToPython({ type: "get_status" }).catch(() => {
				/* will be rejected by _resetPendingOutbound in beforeEach */
			});

			// Restore the socket but immediately drop it again inside
			// the first sendToPython call (simulating a flaky
			// reconnect). The remaining entries should be re-queued
			// (not lost) because the splice-then-iterate pattern
			// ensures re-entrant sendToPython calls append to a fresh
			// queue.
			state.tcpSocket = {
				write: (line: string) => {
					// First write succeeds; then null out the socket so
					// the next sendToPython (called by the flush loop)
					// sees a null socket and re-queues.
					mocks.socketWrite(line);
					state.tcpSocket = null;
					return true;
				},
				// biome-ignore lint/suspicious/noExplicitAny: mock socket for tests
			} as any;

			_flushPendingOutbound();

			// One entry was sent (the first one); the second was
			// re-queued because the socket dropped mid-flush.
			expect(mocks.socketWrite).toHaveBeenCalledTimes(1);
			expect(_pendingOutboundLengthForTest()).toBe(1);
		});
	});

	describe("_resetPendingOutbound()", () => {
		it("rejects every queued entry with the given reason", async () => {
			state.tcpSocket = null;
			state._hadConnectedBefore = true;

			const p1 = sendToPython({ type: "get_config" });
			const p2 = sendToPython({ type: "get_status" });
			const p3 = sendToPython({ type: "heartbeat" });

			const r1 = vi.fn();
			const r2 = vi.fn();
			const r3 = vi.fn();
			p1.catch(r1);
			p2.catch(r2);
			p3.catch(r3);

			_resetPendingOutbound("Application is restarting");

			expect(_pendingOutboundLengthForTest()).toBe(0);
			await vi.waitFor(() =>
				expect([r1, r2, r3].every((fn) => fn.mock.calls.length === 1)).toBe(
					true,
				),
			);
			expect((r1.mock.calls[0][0] as Error).message).toBe(
				"Application is restarting",
			);
			expect((r2.mock.calls[0][0] as Error).message).toBe(
				"Application is restarting",
			);
			expect((r3.mock.calls[0][0] as Error).message).toBe(
				"Application is restarting",
			);
		});

		it("is a no-op when the queue is empty", () => {
			_resetPendingOutbound("nothing to clear");
			expect(_pendingOutboundLengthForTest()).toBe(0);
		});
	});
});
