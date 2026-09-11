/**
 * Regression tests: the microphone-test review panel's no-speech-model
 * state must render TRANSLATED text, never a raw i18n key.
 *
 * The "Estimated Transcription Quality" row renders an explicit
 * not-applicable value when `transcriptionUnavailable` is set (no speech
 * model loaded, the fresh-install state). A historical key-path typo
 * (one missing `qualityFeedback.` path segment) made `t()` fall through
 * its lookup chain to the raw-key fallback, so every locale rendered the
 * literal string "microphoneTest.qualityNotApplicable" in bold text.
 *
 * These tests mount the real component with the REAL `t()` (no i18n mock)
 * so the actual lookup chain, locale map → primary subtag → English →
 * raw key, runs exactly as in production. If the call-site key drifts
 * from the en.json catalog again, the rendered text is the raw key and
 * the assertions fail.
 */
import { cleanup, render, screen } from "@testing-library/react";
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import enMessages from "@/i18n/translations/en.json";

// Flatten the nested en.json into dot-separated keys, mirroring what the
// real i18n store does at load time. Used to assert the RENDERED text
// matches the catalog VALUE (so the test stays locale-source-accurate
// without duplicating the English copy inline).
const enFlat = new Map<string, string>();
function flatten(obj: Record<string, unknown>, prefix = ""): void {
	for (const [k, v] of Object.entries(obj)) {
		const key = prefix ? `${prefix}.${k}` : k;
		if (v && typeof v === "object") {
			flatten(v as Record<string, unknown>, key);
		} else if (typeof v === "string") {
			enFlat.set(key, v);
		}
	}
}
flatten(enMessages as Record<string, unknown>);

function enText(key: string): string {
	const value = enFlat.get(key);
	if (value === undefined) {
		throw new Error(`Missing en.json key: ${key}`);
	}
	return value;
}

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: ({
		children,
		icon,
	}: {
		children?: React.ReactNode;
		icon?: { name?: string };
	}) => (
		<span data-testid="hugeicon" data-name={icon?.name}>
			{children}
		</span>
	),
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

const qualityData = {
	volume_level: "good" as const,
	volume_rms: 0.1,
	peak_level: 0.5,
	noise_level: "moderate" as const,
	has_voice: true,
	has_clipping: false,
	detected_issues: [],
	estimated_transcription_quality: 0,
	silence_ratio: 0.2,
};

function renderNoModelPanel() {
	render(
		<TestReviewPanel
			durationMs={5000}
			quality={qualityData}
			transcription={null}
			transcriptionUnavailable={true}
			testAudioBase64="data:audio/wav;base64,AAAA"
			rawAudioBase64={null}
			playing={false}
			playingOriginal={false}
			onPlayEnhanced={() => {}}
			onPlayOriginal={() => {}}
			onStop={() => {}}
			onRetest={() => {}}
			hasFiltersEnabled={false}
		/>,
	);
}

describe("TestReviewPanel no-model state renders translated text (never a raw key)", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the localized not-applicable value for the estimated-quality row", () => {
		renderNoModelPanel();

		// The not-applicable value must be the en.json VALUE for the
		// microphoneTest.qualityFeedback.qualityNotApplicable key
		// ("N/A: transcription unavailable"), translated, not the key.
		expect(
			screen.getByText(
				enText("microphoneTest.qualityFeedback.qualityNotApplicable"),
			),
		).toBeTruthy();
	});

	it("never renders the raw translation key anywhere in the panel", () => {
		renderNoModelPanel();

		// The historical bug rendered the literal key string because the
		// call-site key was missing one path segment. Assert NO raw i18n
		// key (dot-separated, camelCase) leaks into the visible text.
		const panelText = document.body.textContent ?? "";
		expect(panelText).not.toContain("microphoneTest.qualityNotApplicable");
		expect(panelText).not.toContain("qualityFeedback.qualityNotApplicable");
		expect(panelText).not.toMatch(/\bmicrophoneTest\.[a-zA-Z.]+\b/);
	});

	it("still renders the numeric quality score when a model IS available", () => {
		render(
			<TestReviewPanel
				durationMs={5000}
				quality={{ ...qualityData, estimated_transcription_quality: 72 }}
				transcription="hello world"
				transcriptionUnavailable={false}
				testAudioBase64="data:audio/wav;base64,AAAA"
				rawAudioBase64={null}
				playing={false}
				playingOriginal={false}
				onPlayEnhanced={() => {}}
				onPlayOriginal={() => {}}
				onStop={() => {}}
				onRetest={() => {}}
				hasFiltersEnabled={false}
			/>,
		);

		// With a model available the row shows the computed percentage —
		// the not-applicable branch must not swallow the numeric path.
		expect(screen.getByText("72%")).toBeTruthy();
		expect(
			screen.queryByText(
				enText("microphoneTest.qualityFeedback.qualityNotApplicable"),
			),
		).toBeNull();
	});
});
