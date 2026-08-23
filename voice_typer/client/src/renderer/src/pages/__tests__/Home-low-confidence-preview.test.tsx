/**
 * Component tests for the Home last-transcription preview's
 * low-confidence warning + Re-dictate affordance
 * (pages/home/components/LastTranscriptionPreview.tsx).
 *
 * Pins the rendering contract that folds the engine-reported
 * `TranscriptionQualitySummary` into UI:
 *   - The inline "may be inaccurate" warning is HIDDEN when the
 *     quality summary is absent (undefined / null) or when both
 *     metrics sit within thresholds (mean_logprob >= -1.0 AND
 *     no_speech_prob_max <= 0.6).
 *   - The warning SHOWS when mean_logprob < -1.0 OR
 *     no_speech_prob_max > 0.6.
 *   - The Re-dictate button invokes the provided onRedictate callback,
 *     and renders only when that callback is supplied.
 */
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/__tests__/helpers/render";
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
} from "@/__tests__/helpers/stableMocks";
import { LastTranscriptionPreview } from "@/pages/home/components/LastTranscriptionPreview";
import type { TranscriptionQualitySummary } from "@/types/ipc";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

afterEach(() => {
	cleanup();
});

function renderPreview(
	quality?: TranscriptionQualitySummary | null,
	onRedictate?: () => void,
) {
	return renderWithProviders(
		<LastTranscriptionPreview
			text="sample dictation output"
			onUndo={() => {}}
			onRepaste={() => {}}
			quality={quality}
			onRedictate={onRedictate}
		/>,
	);
}

function queryWarning(): HTMLElement | null {
	return screen.queryByText(/may be inaccurate/i);
}

describe("LastTranscriptionPreview low-confidence warning visibility", () => {
	it("hides the warning when quality is undefined", () => {
		renderPreview(undefined);
		expect(queryWarning()).toBeNull();
	});

	it("hides the warning when quality is null", () => {
		renderPreview(null);
		expect(queryWarning()).toBeNull();
	});

	it("hides the warning when both metrics are within thresholds", () => {
		renderPreview({ mean_logprob: -0.4, no_speech_prob_max: 0.25 });
		expect(queryWarning()).toBeNull();
	});

	it("hides the warning at the exact boundary values", () => {
		renderPreview({ mean_logprob: -1.0, no_speech_prob_max: 0.6 });
		expect(queryWarning()).toBeNull();
	});

	it("shows the warning when mean_logprob < -1.0", () => {
		renderPreview({ mean_logprob: -1.6, no_speech_prob_max: 0.2 });
		expect(queryWarning()).not.toBeNull();
	});

	it("shows the warning when no_speech_prob_max > 0.6", () => {
		renderPreview({ mean_logprob: -0.3, no_speech_prob_max: 0.9 });
		expect(queryWarning()).not.toBeNull();
	});

	it("shows the warning from a partial summary with only one signal", () => {
		renderPreview({ no_speech_prob_max: 0.75 });
		expect(queryWarning()).not.toBeNull();
	});
});

describe("LastTranscriptionPreview Re-dictate affordance", () => {
	it("invokes the onRedictate callback on click", () => {
		const onRedictate = vi.fn();
		renderPreview({ mean_logprob: -1.8 }, onRedictate);

		const redictateButton = screen.getByRole("button", {
			name: "Record again",
		});
		fireEvent.click(redictateButton);
		expect(onRedictate).toHaveBeenCalledTimes(1);
	});

	it("renders the Re-dictate affordance only alongside the warning", () => {
		renderPreview({ mean_logprob: -0.2, no_speech_prob_max: 0.1 }, vi.fn());
		expect(screen.queryByRole("button", { name: "Record again" })).toBeNull();
	});

	it("omits the Re-dictate affordance when no callback is provided", () => {
		renderPreview({ mean_logprob: -1.8 });
		expect(screen.queryByRole("button", { name: "Record again" })).toBeNull();
		expect(queryWarning()).not.toBeNull();
	});
});
