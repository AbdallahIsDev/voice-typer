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
});
