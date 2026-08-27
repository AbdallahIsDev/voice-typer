/**
 * Regression tests: `useMicrophoneLevelMonitor` bounded-start-retry.
 *
 * Background
 * ----------
 * On a cold start with the Microphone page restored (persisted last
 * page), the mount effect fires ``level_monitor_start`` the moment the
 * config round-trip lands — which can be while the host bridge is still
 * establishing (renderer connects before the backend finishes booting).
 * A rejected/failed start used to be terminal (console warn only), so
 * the live level bar stayed dead for the page's entire lifetime and the
 * user had to switch pages to recover it. The fix schedules a bounded
 * backoff retry chain (1s → 2s → 4s) owned by the effect instance:
 *
 *   1. A failed start retries up to 3 times, then gives up (no infinite
 *      loop against a genuinely broken backend).
 *   2. A ``client.consent_required`` refusal is terminal — the consent
 *      dialog's onAllow path restarts the monitor explicitly.
 *   3. A pending retry is cancelled on unmount / dep change (the
 *      cleanup's ``level_monitor_stop`` owns teardown).
 */
import { act, cleanup, render } from "@testing-library/react";
import type { MutableRefObject, ReactNode, RefObject } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks ───────────────────────────────────────────────────────────
const callMock = vi.fn();
const usePythonEventMock = vi.fn();

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: callMock,
		status: "connected",
		connectionStatus: "connected",
	}),
	usePythonEvent: usePythonEventMock,
}));

vi.mock("@/types/config", () => ({}));

// rAF stub: keep the hook's rAF loop deterministic and inert (the
// retry tests exercise the start lifecycle, not the frame loop).
let rafCount = 0;
beforeEach(() => {
	rafCount = 0;
	vi.stubGlobal(
		"requestAnimationFrame",
		vi.fn((cb: () => void) => {
			void cb;
			rafCount += 1;
			return rafCount;
		}),
	);
	vi.stubGlobal("cancelAnimationFrame", vi.fn());
	callMock.mockReset();
	usePythonEventMock.mockReset();
});

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
	vi.useRealTimers();
});

function makeRefs(): {
	playingRef: MutableRefObject<boolean>;
	testRunningRef: MutableRefObject<boolean>;
	meterRef: RefObject<HTMLElement | null>;
} {
	const playingRef: MutableRefObject<boolean> = { current: false };
	const testRunningRef: MutableRefObject<boolean> = { current: false };
	const meterDiv = document.createElement("div");
	const meterRef: RefObject<HTMLElement | null> = { current: meterDiv };
	return { playingRef, testRunningRef, meterRef };
}

async function renderProbe(
	refs: ReturnType<typeof makeRefs>,
	onConsentRequired?: (field?: string) => void,
) {
	const { useMicrophoneLevelMonitor } = await import(
		"../useMicrophoneLevelMonitor"
	);
	function Probe() {
		useMicrophoneLevelMonitor({
			// Biometric consent granted so the mount effect's
			// ``level_monitor_start`` (gated on ``voice_biometric_consent``)
			// still fires.
			config: {
				microphone: null,
				voice_biometric_consent: true,
			} as unknown as Parameters<typeof useMicrophoneLevelMonitor>[0]["config"],
			playingRef: refs.playingRef,
			testRunningRef: refs.testRunningRef,
			meterRef: refs.meterRef,
			onConsentRequired,
		});
		return null as unknown as ReactNode;
	}
	const utils = render(<Probe />);
	return { ...utils };
}

function countStartCalls(): number {
	return callMock.mock.calls.filter((c) => c[0] === "level_monitor_start")
		.length;
}

describe("useMicrophoneLevelMonitor — bounded level_monitor_start retry", () => {
	it("retries a failed start on the backoff schedule and stops after success", async () => {
		vi.useFakeTimers();
		let startCalls = 0;
		const transientErr = new Error("bridge not ready");
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "level_monitor_start") {
				startCalls += 1;
				// Fail the initial start + the first retry, succeed on the
				// second retry (boot-window bridge rejection recovering).
				return startCalls <= 2
					? Promise.reject(transientErr)
					: Promise.resolve({ success: true });
			}
			return Promise.resolve({ success: true });
		});

		const refs = makeRefs();
		await renderProbe(refs);

		// Initial start fired and failed → retry scheduled at +1s.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(countStartCalls()).toBe(1);

		// +1s → first retry fires and fails → next retry at +2s.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(1000);
		});
		expect(countStartCalls()).toBe(2);

		// +2s → second retry fires and SUCCEEDS → chain stops.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(2000);
		});
		expect(countStartCalls()).toBe(3);

		// No further retries after success.
		const after = countStartCalls();
		await act(async () => {
			await vi.advanceTimersByTimeAsync(30_000);
		});
		expect(countStartCalls()).toBe(after);
	});

	it("stops after the retry budget when the backend keeps failing (no infinite loop)", async () => {
		vi.useFakeTimers();
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "level_monitor_start") {
				return Promise.reject(new Error("backend down"));
			}
			return Promise.resolve({ success: true });
		});

		const refs = makeRefs();
		await renderProbe(refs);

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		// 1 initial + 3 retries (1s + 2s + 4s backoff) = 4 total, then
		// the budget is spent and no further timers are scheduled.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(30_000);
		});
		expect(countStartCalls()).toBe(4);
	});

	it("does NOT retry when the refusal is client.consent_required (dialog owns recovery)", async () => {
		vi.useFakeTimers();
		const consentErr = new Error(
			"voice biometric consent required to start level monitor",
		);
		(consentErr as { code?: string }).code = "client.consent_required";
		(consentErr as { consent_field?: string }).consent_field =
			"voice_biometric_consent";
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "level_monitor_start") {
				return Promise.reject(consentErr);
			}
			return Promise.resolve({ success: true });
		});
		const onConsentRequired = vi.fn();

		const refs = makeRefs();
		await renderProbe(refs, onConsentRequired);

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(onConsentRequired).toHaveBeenCalledTimes(1);
		expect(onConsentRequired).toHaveBeenCalledWith("voice_biometric_consent");
		expect(countStartCalls()).toBe(1);

		// Advancing well past the retry budget must not add retries.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(30_000);
		});
		expect(countStartCalls()).toBe(1);
		expect(onConsentRequired).toHaveBeenCalledTimes(1);
	});

	it("cancels the pending retry on unmount (teardown owns the stream)", async () => {
		vi.useFakeTimers();
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "level_monitor_start") {
				return Promise.reject(new Error("bridge not ready"));
			}
			return Promise.resolve({ success: true });
		});

		const refs = makeRefs();
		const utils = await renderProbe(refs);

		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(countStartCalls()).toBe(1);

		// Unmount BEFORE the +1s retry fires — the cleanup must cancel
		// the scheduled retry so no start outlives its effect instance.
		act(() => {
			utils.unmount();
		});
		await act(async () => {
			await vi.advanceTimersByTimeAsync(10_000);
		});
		expect(countStartCalls()).toBe(1);
	});
});
