/**
 * Unit tests for `usePackDownload`.
 *
 * Coverage:
 *   - initial state is `{ status: "idle", error: null, isReady: false }`
 *   - each of the 11 subscribed push events drives the correct state
 *     transition (see the state-machine comment in `usePackDownload.ts`)
 *   - `error` is recorded on failure / crash / corruption events and
 *     cleared on `pack_ready`
 *   - `isReady` is `true` ONLY when `status === "ready"`
 *   - `worker_unloaded` only transitions from "ready" → "worker-unloaded"
 *     (a stray late-arriving event from any other state is a no-op)
 *   - `pack_verified` / `worker_started` don't downgrade "ready"
 *
 * Strategy: renderHook with `usePythonEvent` mocked to capture the 11
 * per-event handlers. Tests invoke the captured handler with a fake
 * payload and assert on `result.current`.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks (hoisted so vi.mock factories can reference them) ───────────
const { usePythonEventMock } = vi.hoisted(() => ({
	usePythonEventMock: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: usePythonEventMock,
}));

import { usePackDownload } from "@/hooks/usePackDownload";

// ── Helpers ──────────────────────────────────────────────────────────

/** Pull the handler captured by the usePythonEvent mock for a given
 *  event name. Throws if no handler was registered for that event —
 *  tests should fail loudly if a subscription was dropped. */
function getHandler(
	eventName: string,
): (data?: Record<string, unknown>) => (() => void) | undefined {
	const call = usePythonEventMock.mock.calls.find((c) => c[0] === eventName);
	if (!call) {
		throw new Error(
			`no usePythonEvent subscription found for "${eventName}" — ` +
				`got calls for: ${usePythonEventMock.mock.calls
					.map((c) => c[0])
					.join(", ")}`,
		);
	}
	return call[1] as (
		data?: Record<string, unknown>,
	) => (() => void) | undefined;
}

beforeEach(() => {
	usePythonEventMock.mockReset();
});

afterEach(() => {
	vi.clearAllMocks();
});

// ── Tests ────────────────────────────────────────────────────────────

describe("usePackDownload — initial state", () => {
	it("subscribes to all 11 pack/worker lifecycle push events", () => {
		renderHook(() => usePackDownload());

		const expectedEvents = [
			"pack_download_started",
			"pack_download_progress",
			"pack_download_completed",
			"pack_download_failed",
			"pack_verified",
			"pack_missing",
			"pack_corrupt",
			"pack_ready",
			"worker_started",
			"worker_crashed",
			"worker_unloaded",
		];
		const subscribed = usePythonEventMock.mock.calls.map((c) => c[0]);
		for (const name of expectedEvents) {
			expect(subscribed, `missing subscription for ${name}`).toContain(name);
		}
		expect(usePythonEventMock.mock.calls.length).toBe(expectedEvents.length);
	});

	it("initial state is idle + no error + not ready", () => {
		const { result } = renderHook(() => usePackDownload());
		expect(result.current.status).toBe("idle");
		expect(result.current.error).toBeNull();
		expect(result.current.isReady).toBe(false);
	});
});

describe("usePackDownload — download lifecycle transitions", () => {
	it("pack_download_started → downloading + clears error", () => {
		const { result } = renderHook(() => usePackDownload());

		// Seed an error first so we can verify it's cleared.
		act(() => getHandler("pack_download_failed")({ error: "first fail" }));
		expect(result.current.error).toBe("first fail");

		act(() => getHandler("pack_download_started")());

		expect(result.current.status).toBe("downloading");
		expect(result.current.error).toBeNull();
		expect(result.current.isReady).toBe(false);
	});

	it("pack_download_progress is silent (no status change unless from idle)", () => {
		const { result } = renderHook(() => usePackDownload());

		// From idle, progress flips to downloading (catches up if
		// `pack_download_started` was missed — e.g. renderer mounted
		// after the download already began).
		act(() => getHandler("pack_download_progress")({ percent: 12 }));
		expect(result.current.status).toBe("downloading");

		// From downloading, progress is a no-op (status stays).
		act(() => getHandler("pack_download_progress")({ percent: 50 }));
		expect(result.current.status).toBe("downloading");
	});

	it("pack_download_completed → verifying", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_download_completed")());
		expect(result.current.status).toBe("verifying");
		expect(result.current.isReady).toBe(false);
	});

	it("pack_download_failed records error from data.error", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_download_failed")({ error: "disk full" }));
		expect(result.current.status).toBe("failed");
		expect(result.current.error).toBe("disk full");
		expect(result.current.isReady).toBe(false);
	});

	it("pack_download_failed falls back to data.message / data.reason", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_download_failed")({ message: "timeout" }));
		expect(result.current.error).toBe("timeout");

		// A different failure event with `reason` should overwrite.
		act(() => getHandler("pack_download_failed")({ reason: "proxy 502" }));
		expect(result.current.error).toBe("proxy 502");
	});

	it("pack_download_failed leaves existing error in place when payload has no string field", () => {
		const { result } = renderHook(() => usePackDownload());
		// Seed an error first.
		act(() => getHandler("pack_download_failed")({ error: "first" }));
		// A second failure with no message field preserves the prior error
		// (a transient progress event shouldn't wipe a recorded failure
		// message — see the comment in `usePackDownload.ts`).
		act(() => getHandler("pack_download_failed")({ code: 42 }));
		expect(result.current.error).toBe("first");
	});
});

describe("usePackDownload — pack verification + worker readiness", () => {
	it("pack_verified transitions idle → worker-starting (pack OK, worker pending)", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_verified")());
		expect(result.current.status).toBe("worker-starting");
		expect(result.current.isReady).toBe(false);
	});

	it("pack_verified does NOT downgrade ready (late event is a no-op)", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_ready")());
		expect(result.current.status).toBe("ready");
		// A stray late-arriving pack_verified must not regress the status.
		act(() => getHandler("pack_verified")());
		expect(result.current.status).toBe("ready");
		expect(result.current.isReady).toBe(true);
	});

	it("worker_started transitions idle → worker-starting", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("worker_started")());
		expect(result.current.status).toBe("worker-starting");
		expect(result.current.isReady).toBe(false);
	});

	it("worker_started does NOT downgrade ready", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_ready")());
		act(() => getHandler("worker_started")());
		expect(result.current.status).toBe("ready");
	});

	it("pack_ready is terminal — clears error and sets isReady", () => {
		const { result } = renderHook(() => usePackDownload());
		// Seed a failure first.
		act(() => getHandler("pack_download_failed")({ error: "transient" }));
		expect(result.current.error).toBe("transient");

		// pack_ready clears the error and flips isReady.
		act(() => getHandler("pack_ready")());
		expect(result.current.status).toBe("ready");
		expect(result.current.error).toBeNull();
		expect(result.current.isReady).toBe(true);
	});
});

describe("usePackDownload — pack missing / corrupt (§8.2 / §8.10)", () => {
	it("pack_missing → missing status", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_missing")());
		expect(result.current.status).toBe("missing");
		expect(result.current.isReady).toBe(false);
	});

	it("pack_corrupt → corrupt status + records reason", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_corrupt")({ reason: "sha256 mismatch" }));
		expect(result.current.status).toBe("corrupt");
		expect(result.current.error).toBe("sha256 mismatch");
		expect(result.current.isReady).toBe(false);
	});

	it("pack_corrupt falls back to error / message when reason is absent", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_corrupt")({ error: "size mismatch" }));
		expect(result.current.error).toBe("size mismatch");
	});
});

describe("usePackDownload — worker crash + unload", () => {
	it("worker_crashed → worker-crashed + records reason", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("worker_crashed")({ reason: "SIGSEGV" }));
		expect(result.current.status).toBe("worker-crashed");
		expect(result.current.error).toBe("SIGSEGV");
		expect(result.current.isReady).toBe(false);
	});

	it("worker_unloaded only transitions from ready → worker-unloaded", () => {
		const { result } = renderHook(() => usePackDownload());

		// From idle: stray event is a no-op (defensive — see comment
		// in usePackDownload.ts about avoiding wiping failed/missing/
		// corrupt status on a late-arriving worker_unloaded).
		act(() => getHandler("worker_unloaded")());
		expect(result.current.status).toBe("idle");

		// Go to ready, then unload.
		act(() => getHandler("pack_ready")());
		expect(result.current.status).toBe("ready");
		act(() => getHandler("worker_unloaded")());
		expect(result.current.status).toBe("worker-unloaded");
		expect(result.current.isReady).toBe(false);
	});

	it("worker_unloaded does not clobber a failed status", () => {
		const { result } = renderHook(() => usePackDownload());
		act(() => getHandler("pack_download_failed")({ error: "network" }));
		expect(result.current.status).toBe("failed");
		// A stray worker_unloaded must not wipe the failed status.
		act(() => getHandler("worker_unloaded")());
		expect(result.current.status).toBe("failed");
	});
});

describe("usePackDownload — full happy-path sequence", () => {
	it("download → completed → verified → worker_started → pack_ready", () => {
		const { result } = renderHook(() => usePackDownload());

		act(() => getHandler("pack_download_started")());
		expect(result.current.status).toBe("downloading");

		act(() => getHandler("pack_download_progress")({ percent: 50 }));
		expect(result.current.status).toBe("downloading");

		act(() => getHandler("pack_download_completed")());
		expect(result.current.status).toBe("verifying");

		act(() => getHandler("pack_verified")());
		expect(result.current.status).toBe("worker-starting");

		act(() => getHandler("worker_started")());
		expect(result.current.status).toBe("worker-starting");

		act(() => getHandler("pack_ready")());
		expect(result.current.status).toBe("ready");
		expect(result.current.error).toBeNull();
		expect(result.current.isReady).toBe(true);
	});
});
