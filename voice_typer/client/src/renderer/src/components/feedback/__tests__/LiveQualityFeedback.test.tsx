/**
 * Tests for LiveQualityFeedback after the noisy voice-quality status
 * line removal (the live LevelBar already communicates input level).
 *
 * The component now renders ONLY the single test timer readout
 * ("Recording MM:SS / MM:SS") while recording, and nothing otherwise.
 * The removed "Waiting for voice…" / "✓ Voice detected" / quality-tier
 * messages must NOT reappear — they duplicated what the level bar
 * already shows.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is HOISTED to the top of the file (before any imports), so
// the mocked `t` is in place by the time LiveQualityFeedback imports
// it. We use importOriginal to preserve setLocale / registerTranslations
// (which the test doesn't override) and only mock `t` when the
// `useSentinel` flag is set.
let useSentinel = false;
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			if (useSentinel) {
				return `SENTINEL:${key}:${JSON.stringify(params ?? {})}`;
			}
			return actual.t(key, params);
		},
	};
});

import { LiveQualityFeedback } from "@/components/feedback/LiveQualityFeedback";

const baseProps = {
	isRecording: true,
	elapsedSeconds: 3,
	totalSeconds: 10,
};

describe("LiveQualityFeedback — single timer readout", () => {
	afterEach(() => {
		cleanup();
		useSentinel = false;
	});

	beforeEach(() => {
		useSentinel = false;
	});

	it("renders nothing when isRecording is false (preserved behavior)", () => {
		const { container } = render(
			<LiveQualityFeedback {...baseProps} isRecording={false} />,
		);
		expect(container.firstChild).toBeNull();
	});

	it("renders the progressing timer 00:03 / 00:10 from the i18n catalog", () => {
		render(<LiveQualityFeedback {...baseProps} />);
		expect(screen.getByText("Recording… 00:03 / 00:10")).toBeInTheDocument();
	});

	it("pads minutes and seconds (MM:SS format)", () => {
		render(
			<LiveQualityFeedback
				{...baseProps}
				elapsedSeconds={65}
				totalSeconds={600}
			/>,
		);
		expect(screen.getByText("Recording… 01:05 / 10:00")).toBeInTheDocument();
	});

	it("does NOT render the removed voice-quality status messages", () => {
		// The live level bar owns level feedback; these textual states were
		// redundant noise and are permanently removed.
		const { container } = render(<LiveQualityFeedback {...baseProps} />);
		expect(screen.queryByText(/Voice detected/i)).toBeNull();
		expect(screen.queryByText(/Waiting for voice/i)).toBeNull();
		expect(screen.queryByText(/Low volume/i)).toBeNull();
		expect(container.querySelector("[aria-live]")).toBeNull();
	});

	it("renders the t() sentinel when the i18n mock is active (proves no hardcoded literal)", () => {
		useSentinel = true;
		render(<LiveQualityFeedback {...baseProps} />);
		expect(
			screen.getByText(
				/SENTINEL:microphoneTest\.qualityFeedback\.recording:\{\}/,
			),
		).toBeInTheDocument();
	});
});
