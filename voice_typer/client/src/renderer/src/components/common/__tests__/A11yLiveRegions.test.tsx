/**
 * A11yLiveRegions — the app shell's three screen-reader live regions
 * (recording / connection-error / connection-recovery), extracted from
 * App.tsx.
 *
 * Verifies the announcement matrix:
 *   - recording stream (polite): each RecordingState maps to its
 *     announcement; coarse transcribing/loading announcements are
 *     suppressed on the Home page (Home owns its own specific live
 *     region for those transitions).
 *   - connection-error stream (assertive): disconnected / restarting.
 *   - connection-recovery stream (polite): announces only real
 *     recoveries — not the initial connecting → connected transition.
 *
 * The i18n layer is mocked to return raw keys so assertions key on the
 * exact translation keys (stable against copy edits).
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/i18n", () => ({
	useT: () => (key: string) => key,
}));

import { A11yLiveRegions } from "@/components/common/A11yLiveRegions";
import type { ConnectionStatus } from "@/stores/appStore";
import type { Page, RecordingState } from "@/types/ipc";

function renderRegions(
	recordingState: RecordingState,
	connectionStatus: ConnectionStatus,
	prev: ConnectionStatus,
	currentPage: Page = "history",
) {
	return render(
		<A11yLiveRegions
			recordingState={recordingState}
			currentPage={currentPage}
			connectionStatus={connectionStatus}
			prevConnectionRef={{ current: prev }}
		/>,
	);
}

/** The polite recording stream is the FIRST [aria-live=polite] region
 *  in document order (Home's own live region isn't mounted here). */
function recordingRegion(): Element | null {
	return document.querySelector('div[aria-live="polite"]');
}

function errorRegion(): Element | null {
	return document.querySelector('div[aria-live="assertive"]');
}

function recoveryRegion(): Element | null {
	const polite = document.querySelectorAll('div[aria-live="polite"]');
	return polite.length >= 2 ? (polite[polite.length - 1] ?? null) : null;
}

describe("A11yLiveRegions — recording stream (polite)", () => {
	afterEach(() => cleanup());

	it.each([
		["recording", "a11y.recordingStarted"],
		["transcribing", "a11y.transcribingAudio"],
		["idle", "a11y.ready"],
		["error", "a11y.errorOccurred"],
		["loading", "a11y.loadingModel"],
		["cancelling", "a11y.cancelling"],
	] as const)("%s announces its dedicated key", (state, key) => {
		renderRegions(state, "connected", "connected");
		expect(recordingRegion()?.textContent).toContain(key);
	});

	it.each(["transcribing", "loading"] as const)(
		"suppresses the coarse %s announcement on the Home page (Home owns its own live region)",
		(state) => {
			renderRegions(state, "connected", "connected", "home");
			expect(recordingRegion()?.textContent).not.toContain(
				state === "transcribing"
					? "a11y.transcribingAudio"
					: "a11y.loadingModel",
			);
		},
	);

	it("idle stream stays silent about non-active states (no stale concatenation)", () => {
		renderRegions("idle", "connected", "connected");
		const text = recordingRegion()?.textContent ?? "";
		expect(text).toContain("a11y.ready");
		expect(text).not.toContain("a11y.recordingStarted");
		expect(text).not.toContain("a11y.transcribingAudio");
	});
});

describe("A11yLiveRegions — connection error stream (assertive)", () => {
	beforeEach(() => cleanup());
	afterEach(() => cleanup());

	it("disconnected announces the lost-connection key assertively", () => {
		renderRegions("idle", "disconnected", "connected");
		expect(errorRegion()?.getAttribute("aria-live")).toBe("assertive");
		expect(errorRegion()?.textContent).toContain("app.lostConnection");
	});

	it("restarting announces the restarting key assertively", () => {
		renderRegions("idle", "restarting", "connected");
		expect(errorRegion()?.textContent).toContain("app.restartingBackend");
	});

	it("connected/connecting states keep the assertive region silent", () => {
		renderRegions("idle", "connected", "connecting");
		expect(errorRegion()?.textContent).toBe("");
	});
});

describe("A11yLiveRegions — connection recovery stream (polite)", () => {
	beforeEach(() => cleanup());
	afterEach(() => cleanup());

	it("announces recovery after an outage (prev disconnected → connected)", () => {
		renderRegions("idle", "connected", "disconnected");
		expect(recoveryRegion()?.textContent).toContain("about.connected");
	});

	it("stays silent on the initial connecting → connected transition", () => {
		renderRegions("idle", "connected", "connecting");
		expect(recoveryRegion()?.textContent).toBe("");
	});

	it("stays silent when already connected (no re-announcement)", () => {
		renderRegions("idle", "connected", "connected");
		expect(recoveryRegion()?.textContent).toBe("");
	});
});
