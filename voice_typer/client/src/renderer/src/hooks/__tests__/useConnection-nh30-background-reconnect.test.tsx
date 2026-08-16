/**
 * Tests for useConnection —  (background reconnect poll).
 *
 * Scenario under test: when the Python backend is unreachable on
 * initial mount, the connection-lifecycle effect retries `get_config`
 * up to 5 times (2s apart). After exhausting the retries, the hook
 * sets `connectionStatus = "disconnected"` and the user sees the
 * "Lost connection" screen.
 *
 * Before , that was the end of the story — the ONLY way to
 * reconnect was for the user to manually click Retry, or for the host
 * bridge to push a `reconnecting`/`reconnected` synthetic event.
 * There was no background re-attempt loop, so a transient outage
 * (e.g. backend restarted while the renderer was idle) required
 * manual intervention.
 *
 * The fix adds a background reconnect poll: while
 * `connectionStatus === "disconnected"`, attempt a single `get_config`
 * every 10s. On success, flip to "connected". Cap at 12 attempts
 * (2 minutes) so a truly-dead backend doesn't spin an interval
 * forever.
 *
 * The test uses fake timers to:
 *   1. Exhaust the initial 5-retry connection lifecycle (all
 *      get_config calls reject) → status becomes "disconnected".
 *   2. Flip get_config to resolve (simulating backend recovery).
 *   3. Advance fake time by the background-reconnect interval (10s).
 *   4. Assert status becomes "connected" (only possible if the
 *      background poll fired and succeeded).
 *
 * A second test verifies the cap: after 12 failed background
 * attempts, the poll stops calling get_config (no infinite polling
 * against a dead backend).
 */
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + the usePython mock factory binding.
import { pythonMock, resetStableMocks, stableMocks } from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());

import { useConnection } from "@/hooks/useConnection";
import { _resetNavigationForTest, useNavigation } from "@/hooks/useNavigation";
import { useAppStore } from "@/stores/appStore";

// Stub localStorage so useNavigation's persisted-nav-state restore
// (and the beforeEach `clear()` below) doesn't blow up in the jsdom
// environment. Same pattern as useTheme-flush-pending-save.test.tsx.
const lsStub: Record<string, string> = {};
const lsMock = {
	getItem: (k: string) => lsStub[k] ?? null,
	setItem: (k: string, v: string) => {
		lsStub[k] = v;
	},
	removeItem: (k: string) => {
		delete lsStub[k];
	},
	clear: () => {
		for (const k of Object.keys(lsStub)) delete lsStub[k];
	},
};
Object.defineProperty(window, "localStorage", {
	value: lsMock,
	configurable: true,
});

/**
 * Minimal harness that mirrors App.tsx: useNavigation restores the
 * persisted page; useConnection probes the backend on mount. We
 * don't render the page label (we assert on the appStore's
 * connectionStatus directly).
 */
function Harness() {
	const { currentPage, navigate } = useNavigation();
	useConnection({
		call: (async (type: string, _data?: Record<string, unknown>) =>
			mockCall(type)) as unknown as <T = unknown>(
			type: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		currentPage,
		navigate,
	});
	return null;
}

/**
 * Read the current connectionStatus from the Zustand store without
 * subscribing (so the test can poll without re-rendering).
 */
function readStatus(): string {
	return useAppStore.getState().connectionStatus;
}

describe("useConnection — NH-30 background reconnect poll", () => {
	beforeEach(() => {
		resetStableMocks();
		localStorage.clear();
		// Shared store: reset the module-level nav state so a previous
		// test's navigation can't leak into this one.
		_resetNavigationForTest();
		vi.resetModules();
		// Reset the Zustand store state between tests so the
		// "disconnected" status from a previous test doesn't leak.
		useAppStore.getState().setConnectionStatus("connecting");
		useAppStore.getState().setLastError(null);
	});

	afterEach(() => {
		cleanup();
		if (vi.isFakeTimers()) {
			vi.useRealTimers();
		}
	});

	it("auto-recovers to connected when backend becomes reachable again (background poll)", async () => {
		vi.useFakeTimers({ shouldAdvanceTime: true });

		// Phase 1: get_config always rejects → initial 5 retries
		// exhaust → status flips to "disconnected".
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.reject(new Error("down"));
			if (type === "get_status") return Promise.resolve({ status: "idle" });
			if (type === "onboarding_is_first_run")
				return Promise.resolve({ is_first_run: false });
			return Promise.resolve({});
		});

		render(<Harness />);

		// Advance fake time past the 5 initial retries (5 × 2s = 10s).
		// Each retry's setTimeout(2s) is fired, each rejecting. After
		// the 5th failure the connection-lifecycle effect gives up and
		// sets status="disconnected".
		await act(async () => {
			await vi.advanceTimersByTimeAsync(11_000);
		});
		expect(readStatus()).toBe("disconnected");

		// Phase 2: backend recovers — make get_config resolve.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config")
				return Promise.resolve({ onboarding_completed: true });
			if (type === "get_status") return Promise.resolve({ status: "idle" });
			if (type === "onboarding_is_first_run")
				return Promise.resolve({ is_first_run: false });
			return Promise.resolve({});
		});

		// Phase 3: advance fake time by the background-reconnect
		// interval (10s). The first background poll fires and
		// succeeds → status flips to "connected".
		await act(async () => {
			await vi.advanceTimersByTimeAsync(10_500);
		});

		expect(readStatus()).toBe("connected");
	});

	it("stops polling after MAX_BACKGROUND_RECONNECTS attempts (no infinite loop)", async () => {
		vi.useFakeTimers();

		// get_config always rejects — the backend is truly dead.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.reject(new Error("down"));
			if (type === "get_status") return Promise.resolve({ status: "idle" });
			if (type === "onboarding_is_first_run")
				return Promise.resolve({ is_first_run: false });
			return Promise.resolve({});
		});

		render(<Harness />);

		// Exhaust the initial 5 retries → "disconnected". Each retry
		// is 2s apart, so 11s of fake time covers all 5 attempts.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(11_000);
		});
		expect(readStatus()).toBe("disconnected");

		// Advance fake time past the 12-attempt background cap.
		// 12 × 10s = 120s, plus the 10s initial delay = 130s. Use
		// 140s to give the 12th call time to fire.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(140_000);
		});

		// Snapshot get_config count AFTER the cap should have fired.
		const callsAfterCap = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_config",
		).length;

		// Advance ANOTHER 120s of fake time. If the cap is working,
		// NO additional get_config calls should land — the background
		//poll has already stopped. (Before 's cap, the poll
		// would keep firing every 10s indefinitely — 12 more calls
		// in 120s.)
		await act(async () => {
			await vi.advanceTimersByTimeAsync(120_000);
		});

		const callsAfterExtraWait = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_config",
		).length;

		// The count must NOT have grown during the extra 120s wait —
		// that's the proof the poll stopped at the cap. We allow a
		// small slack of 1 (in case a final scheduled call lands
		// microscopically after the cap window).
		const delta = callsAfterExtraWait - callsAfterCap;
		expect(delta).toBeLessThanOrEqual(1);
	});
});
