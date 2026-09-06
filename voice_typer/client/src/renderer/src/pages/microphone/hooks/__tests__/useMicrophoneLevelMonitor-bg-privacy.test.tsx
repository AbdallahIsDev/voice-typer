/**
 * Regression: microphone level monitor must not activate while
 * document is hidden (background/autostart with persisted microphone page).
 *
 * See C-BG-1: persisted vt_nav_state="microphone" while hidden must not
 * start the InputStream; the OS mic indicator would otherwise appear
 * invisibly in the background.
 */
import { act, cleanup, render } from "@testing-library/react";
import type { MutableRefObject, ReactNode, RefObject } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function makeRefs(): {
	playingRef: MutableRefObject<boolean>;
	testRunningRef: MutableRefObject<boolean>;
	meterRef: RefObject<HTMLElement | null>;
} {
	const playingRef: MutableRefObject<boolean> = { current: false };
	const testRunningRef: MutableRefObject<boolean> = { current: false };
	const meterDiv = document.createElement("div");
	const progress = document.createElement("div");
	progress.setAttribute("role", "progressbar");
	const fill = document.createElement("div");
	progress.appendChild(fill);
	meterDiv.appendChild(progress);
	const meterRef: RefObject<HTMLElement | null> = { current: meterDiv };
	return { playingRef, testRunningRef, meterRef };
}

async function renderProbe(refs: ReturnType<typeof makeRefs>) {
	const { useMicrophoneLevelMonitor } = await import(
		"../useMicrophoneLevelMonitor"
	);
	function Probe() {
		useMicrophoneLevelMonitor({
			config: {
				microphone: null,
				voice_biometric_consent: true,
			} as unknown as Parameters<typeof useMicrophoneLevelMonitor>[0]["config"],
			playingRef: refs.playingRef,
			testRunningRef: refs.testRunningRef,
			meterRef: refs.meterRef,
		});
		return null as unknown as ReactNode;
	}
	const utils = render(<Probe />);
	return utils;
}

function setVisibility(state: string) {
	Object.defineProperty(document, "visibilityState", {
		value: state,
		configurable: true,
		writable: true,
	});
	Object.defineProperty(document, "hidden", {
		value: state !== "visible",
		configurable: true,
		writable: true,
	});
}

describe("useMicrophoneLevelMonitor background privacy", () => {
	beforeEach(() => {
		callMock.mockResolvedValue({ success: true });
		// default visible for most tests; individual test can override before render
		setVisibility("visible");
	});

	afterEach(() => {
		cleanup();
		vi.unstubAllGlobals?.();
		callMock.mockReset();
		usePythonEventMock.mockReset();
		// restore visible so other suites are not polluted
		setVisibility("visible");
	});

	it("does NOT call level_monitor_start while document is hidden at mount", async () => {
		setVisibility("hidden");
		const refs = makeRefs();
		await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});
		expect(callMock).not.toHaveBeenCalledWith(
			"level_monitor_start",
			expect.anything(),
		);
	});

	it("defers level_monitor_start until document becomes visible", async () => {
		setVisibility("hidden");
		const refs = makeRefs();
		await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});
		expect(callMock).not.toHaveBeenCalledWith(
			"level_monitor_start",
			expect.anything(),
		);

		// Now make visible and dispatch visibilitychange
		setVisibility("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			// allow deferred startMonitor microtask to run
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});

		expect(callMock).toHaveBeenCalledWith("level_monitor_start", {
			mic_id: null,
		});
	});

	it("starts immediately when document is visible at mount (foreground launch)", async () => {
		setVisibility("visible");
		const refs = makeRefs();
		await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});
		expect(callMock).toHaveBeenCalledWith("level_monitor_start", {
			mic_id: null,
		});
	});

	it("sends level_monitor_stop on unmount after a deferred (hidden→visible) start", async () => {
		// Regression: the hidden-at-mount branch used to early-return a
		// listener-only cleanup, so the monitor started once the page
		// became visible was never stopped on unmount — the OS mic
		// indicator stayed lit with no page active. The deferred path
		// must fall through to the shared ``startedHere``-guarding
		// cleanup that sends ``level_monitor_stop``.
		setVisibility("hidden");
		const refs = makeRefs();
		const utils = await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});
		expect(callMock).not.toHaveBeenCalledWith(
			"level_monitor_start",
			expect.anything(),
		);

		// Page becomes visible → the deferred start fires.
		setVisibility("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(callMock).toHaveBeenCalledWith("level_monitor_start", {
			mic_id: null,
		});

		// Navigate away (unmount) → the stop must be sent.
		await act(async () => {
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});
		callMock.mockClear();
		callMock.mockResolvedValue({ success: true });
		utils.unmount();
		await act(async () => {
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(callMock).toHaveBeenCalledWith("level_monitor_stop");
	});

	it("does NOT send level_monitor_stop when unmounting while still hidden (no deferred start happened)", async () => {
		// Guard the ownership invariant: the shared cleanup only stops a
		// monitor THIS effect instance actually started. Unmounting while
		// still hidden (start never fired) must NOT send a spurious stop.
		setVisibility("hidden");
		const refs = makeRefs();
		const utils = await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});
		callMock.mockClear();
		callMock.mockResolvedValue({ success: true });
		utils.unmount();
		await act(async () => {
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(callMock).not.toHaveBeenCalledWith("level_monitor_stop");
	});

	it("sends level_monitor_stop when the unmount races the in-flight deferred start", async () => {
		// The deferred start's IPC can still be in flight when the page
		// unmounts: the cleanup runs with `startedHere` still false (the
		// start has not resolved yet), so the cleanup-owned stop is skipped
		// — and once the start resolves into a cancelled effect, nothing
		// owned the stop. The backend stream would keep running with no
		// owner: the OS mic indicator lit with no page active. The start's
		// own resolution path must send the matching stop once the
		// in-flight start settles (no later run having taken ownership).
		setVisibility("hidden");
		const refs = makeRefs();
		const utils = await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});
		expect(callMock).not.toHaveBeenCalledWith(
			"level_monitor_start",
			expect.anything(),
		);

		// Make the deferred start IPC stay in flight across the unmount.
		let resolveStart!: (value: { success: boolean }) => void;
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "level_monitor_start") {
				return new Promise<{ success: boolean }>((resolve) => {
					resolveStart = resolve;
				});
			}
			return Promise.resolve({ success: true });
		});
		callMock.mockClear();

		// Page becomes visible → the deferred start is ISSUED (pending).
		setVisibility("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			await Promise.resolve();
		});
		expect(callMock).toHaveBeenCalledWith("level_monitor_start", {
			mic_id: null,
		});

		// Unmount BEFORE the start IPC resolves, then let the start settle.
		utils.unmount();
		await act(async () => {
			resolveStart({ success: true });
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});

		// Exactly ONE stop, from the in-flight start's resolution-time
		// teardown — no leak, and no duplicate stop.
		expect(
			callMock.mock.calls.filter((c) => c[0] === "level_monitor_stop").length,
		).toBe(1);
	});

	it("sends exactly ONE level_monitor_stop on the normal deferred path (no double-stop)", async () => {
		// Dedupe guard for the resolution-time stop: when the start
		// resolves NORMALLY (startedHere=true), the cleanup owns the single
		// stop and the resolution path must not add a second one.
		setVisibility("hidden");
		const refs = makeRefs();
		const utils = await renderProbe(refs);
		await act(async () => {
			await Promise.resolve();
		});

		// Deferred start resolves normally (mock resolves immediately).
		setVisibility("visible");
		await act(async () => {
			document.dispatchEvent(new Event("visibilitychange"));
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(callMock).toHaveBeenCalledWith("level_monitor_start", {
			mic_id: null,
		});

		callMock.mockClear();
		callMock.mockResolvedValue({ success: true });
		utils.unmount();
		await act(async () => {
			await Promise.resolve();
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "level_monitor_stop").length,
		).toBe(1);
	});
});
