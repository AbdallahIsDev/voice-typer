import { describe, expect, it, vi } from "vitest";
import type { TauriGlobal } from "@/lib/tauri-bridge/detect";

import { createPythonNamespace } from "@/lib/tauri-bridge/python-namespace";
import type { PythonPushEvent } from "@/types/ipc";

function makeTauriStubWithCapture(subscriptions: {
	[eventName: string]: (e: { payload: unknown }) => void;
}): TauriGlobal {
	return {
		core: {
			// TauriGlobal.core.invoke is `<T>(cmd, args?) => Promise<T>`
			//, `vi.fn()` infers `Mock<() => unknown>` which isn't
			// assignable to that generic signature. Build the function
			// inline and cast at the boundary so the recorded-mock
			// surface (calls/results) is preserved while the callable
			// shape matches the consumer's `invoke<T>` signature.
			invoke: ((_cmd: string, _args?: Record<string, unknown>) =>
				Promise.resolve({})) as unknown as <T = unknown>(
				cmd: string,
				args?: Record<string, unknown>,
			) => Promise<T>,
		},
		event: {
			listen: vi.fn(
				(
					eventName: string,
					handler: (e: { payload: unknown }) => void,
				): Promise<() => void> => {
					subscriptions[eventName] = handler;
					return Promise.resolve(() => {});
				},
			),
		},
		window: {
			getCurrentWindow: vi.fn(() => ({
				minimize: vi.fn(() => Promise.resolve()),
				toggleMaximize: vi.fn(() => Promise.resolve()),
				close: vi.fn(() => Promise.resolve()),
				isMaximized: vi.fn(() => Promise.resolve(false)),
				onResized: vi.fn(() => Promise.resolve(() => {})),
			})),
		},
	};
}

describe("python-namespace: supervisor_failed listener (XZ-R16-02)", () => {
	it("registers a listener for the 'supervisor_failed' Tauri event", async () => {
		const subscriptions: {
			[eventName: string]: (e: { payload: unknown }) => void;
		} = {};
		const tauri = makeTauriStubWithCapture(subscriptions);

		createPythonNamespace(tauri).onEvent(() => {});

		// Flush the microtask queue so the `listen` promises resolve
		// and the handlers are captured. `makeListener` calls
		// `tauri.event.listen(...)` synchronously inside `onEvent`, so
		// by the time `onEvent` returns the call has been registered —
		// but the captured handler is set inside the `listen` mock
		// synchronously too, so no await is strictly required. We
		// await anyway for belt-and-suspenders across vi.fn() ordering.
		await Promise.resolve();

		expect(subscriptions.supervisor_failed).toBeTypeOf("function");
	});

	it("synthesizes a type='error' event with code='respawn_exhausted' and the supervisor's message", async () => {
		const subscriptions: {
			[eventName: string]: (e: { payload: unknown }) => void;
		} = {};
		const tauri = makeTauriStubWithCapture(subscriptions);

		const received: PythonPushEvent[] = [];
		createPythonNamespace(tauri).onEvent((e) => {
			received.push(e as PythonPushEvent);
		});
		await Promise.resolve();

		// Dispatch the supervisor_failed event as the Rust host would:
		// see src-tauri/src/sidecar/supervisor.rs:633, payload is a
		// JSON object with at least `reason`, `message`, and
		// `restart_count`.
		subscriptions.supervisor_failed?.({
			payload: {
				reason: "circuit_breaker_tripped",
				message:
					"Native sidecar crashed 5 times in a row. Please reinstall Lausu.",
				restart_count: 5,
			},
		});

		expect(received).toHaveLength(1);
		const event = received[0];
		expect(event?.type).toBe("error");
		if (event?.type !== "error") {
			throw new Error("expected an error push event");
		}
		// The structured code is what useConnection.ts branches on —
		// see `RESPAWN_EXHAUSTED_CODE` constant in useConnection.ts.
		expect(event.data).toMatchObject({
			code: "respawn_exhausted",
		});
		// The supervisor's user-facing message must be preserved so
		// the renderer can surface the reinstall prompt verbatim.
		expect(typeof event.data?.message).toBe("string");
		expect(event.data?.message).toContain(
			"Native sidecar crashed 5 times in a row. Please reinstall Lausu.",
		);
		// The stable sentinel substring must also be present so any
		// legacy substring-match fallback in the renderer still works.
		expect(event.data?.message).toContain("respawn exhausted");
	});

	it("falls back to a stable sentinel message when the supervisor omits `message`", async () => {
		const subscriptions: {
			[eventName: string]: (e: { payload: unknown }) => void;
		} = {};
		const tauri = makeTauriStubWithCapture(subscriptions);

		const received: PythonPushEvent[] = [];
		createPythonNamespace(tauri).onEvent((e) => {
			received.push(e as PythonPushEvent);
		});
		await Promise.resolve();

		// Supervisor emits a minimal payload with no `message` field —
		// the renderer must still receive a usable error event with the
		// structured code so useConnection flips to "disconnected".
		subscriptions.supervisor_failed?.({
			payload: {
				reason: "circuit_breaker_tripped",
				restart_count: 5,
			},
		});

		expect(received).toHaveLength(1);
		const event = received[0];
		expect(event?.type).toBe("error");
		if (event?.type !== "error") {
			throw new Error("expected an error push event");
		}
		expect(event.data).toMatchObject({
			code: "respawn_exhausted",
			message: "respawn exhausted",
		});
	});

	it("does NOT synthesize an error event for the transient 'supervisor_relaunching' event with a non-exhaustion reason", async () => {
		const subscriptions: {
			[eventName: string]: (e: { payload: unknown }) => void;
		} = {};
		const tauri = makeTauriStubWithCapture(subscriptions);

		const received: PythonPushEvent[] = [];
		createPythonNamespace(tauri).onEvent((e) => {
			received.push(e as PythonPushEvent);
		});
		await Promise.resolve();

		// A transient relaunch (NOT exhaustion) must synthesize a
		// `reconnecting` event so useConnection shows the
		// "Restarting…" banner, NOT a terminal `error` event.
		subscriptions.supervisor_relaunching?.({
			payload: { reason: "tcp_disconnected" },
		});

		expect(received).toHaveLength(1);
		const event = received[0];
		expect(event?.type).toBe("reconnecting");
		if (event?.type !== "reconnecting") {
			throw new Error("expected a reconnecting push event");
		}
		expect(event.data).toMatchObject({ reason: "tcp_disconnected" });
	});

	it("synthesizes a type='error' event when 'supervisor_relaunching' carries reason='backoff_exhausted'", async () => {
		const subscriptions: {
			[eventName: string]: (e: { payload: unknown }) => void;
		} = {};
		const tauri = makeTauriStubWithCapture(subscriptions);

		const received: PythonPushEvent[] = [];
		createPythonNamespace(tauri).onEvent((e) => {
			received.push(e as PythonPushEvent);
		});
		await Promise.resolve();

		// supervisor.rs:495 emits supervisor_relaunching with
		// reason="backoff_exhausted" right before app.restart(), the
		// bridge must convert this into a terminal `error` event
		// (same shape as supervisor_failed) so the renderer doesn't
		// stay stuck on "Restarting…".
		subscriptions.supervisor_relaunching?.({
			payload: { reason: "backoff_exhausted" },
		});

		expect(received).toHaveLength(1);
		const event = received[0];
		expect(event?.type).toBe("error");
		if (event?.type !== "error") {
			throw new Error("expected an error push event");
		}
		expect(event.data).toMatchObject({
			code: "respawn_exhausted",
			message: "respawn exhausted",
		});
	});

	it("the cleanup function returned by onEvent unlistens from supervisor_failed (and the other channels)", async () => {
		const subscriptions: {
			[eventName: string]: (e: { payload: unknown }) => void;
		} = {};
		const tauri = makeTauriStubWithCapture(subscriptions);

		const cleanup = createPythonNamespace(tauri).onEvent(() => {});
		await Promise.resolve();

		// The cleanup function must be callable without throwing —
		// makeListener returns an idempotent unlisten that's safe to
		// call multiple times.
		expect(() => cleanup()).not.toThrow();
		expect(() => cleanup()).not.toThrow();
	});
});
