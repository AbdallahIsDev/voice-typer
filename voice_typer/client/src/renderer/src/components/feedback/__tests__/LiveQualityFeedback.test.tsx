/**
 *  / A11Y-5: Accessibility + i18n tests for LiveQualityFeedback.
 *
 * The component previously rendered hardcoded English strings and had no
 * aria-live region, so screen readers neither translated nor announced
 * quality/timer changes. After the fix:
 *   - The outer <div> is a role="status" polite aria-live region
 *     (aria-atomic="true") so AT announces updates coherently.
 *   - All visible strings come from the i18n catalog (liveQuality.* keys)
 *     and adapt to the current locale.
 *   - When isRecording is false, nothing renders (preserved behavior).
 *
 * These tests verify the ARIA semantics and the i18n wiring by:
 *   1. Asserting role="status" + aria-live + aria-atomic attributes.
 *   2. Asserting the English text matches the liveQuality.* catalog
 *      entries. The en.json catalog value uses the ellipsis character
 *      `…` (U+2026), while the pre-fix literal used three ASCII dots
 *      `...` — so the text-match assertion FAILS on the old code and
 *      PASSES on the new.
 *   3. Mocking the i18n module to return a sentinel value, then
 *      asserting the sentinel appears in the rendered output — proving
 *      the string flows through t() and is not a hardcoded literal.
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

// Default props that exercise the "voice detected, quality excellent"
// branch. Each test overrides what it needs.
const baseProps = {
	level: 0.1,
	peak: 0.2,
	isRecording: true,
	elapsedSeconds: 30,
	totalSeconds: 60,
};

describe("LiveQualityFeedback — A11Y-5 (aria-live + i18n)", () => {
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

	it('wraps the changing region in role="status" with polite aria-live and aria-atomic', () => {
		render(<LiveQualityFeedback {...baseProps} />);
		const status = screen.getByRole("status");
		expect(status).toBeInTheDocument();
		expect(status).toHaveAttribute("aria-live", "polite");
		expect(status).toHaveAttribute("aria-atomic", "true");
	});

	it("renders the timer text from the liveQuality.recording i18n key (not a literal)", () => {
		render(<LiveQualityFeedback {...baseProps} />);
		// en.json: "Recording… {time} / {total}" with time="00:30", total="01:00".
		// The pre-fix literal was "Recording... " (three ASCII dots). The
		// catalog uses the ellipsis character `…`, so this assertion
		// FAILS on the old code and PASSES on the new.
		expect(screen.getByText("Recording… 00:30 / 01:00")).toBeInTheDocument();
	});

	it("renders voiceDetected text when peak > 0.05", () => {
		render(<LiveQualityFeedback {...baseProps} peak={0.2} />);
		expect(screen.getByText("✓ Voice detected")).toBeInTheDocument();
		expect(screen.queryByText("Waiting for voice…")).toBeNull();
	});

	it("renders waitingForVoice text when peak <= 0.05", () => {
		render(<LiveQualityFeedback {...baseProps} peak={0.01} />);
		expect(screen.getByText("Waiting for voice…")).toBeInTheDocument();
		expect(screen.queryByText("✓ Voice detected")).toBeNull();
	});

	it("renders qualityExcellent when hasVoice and not tooLoud", () => {
		render(<LiveQualityFeedback {...baseProps} peak={0.2} level={0.1} />);
		expect(screen.getByText("Quality: Excellent")).toBeInTheDocument();
	});

	it("renders volumeTooHigh when hasVoice and peak > 0.9 (clipping risk)", () => {
		render(<LiveQualityFeedback {...baseProps} peak={0.95} level={0.1} />);
		expect(
			screen.getByText("⚠ Volume too high (clipping risk)"),
		).toBeInTheDocument();
	});

	it("renders volumeTooLow when level <= 0.005 and no voice", () => {
		render(<LiveQualityFeedback {...baseProps} peak={0.01} level={0.001} />);
		expect(screen.getByText("⚠ Volume too low")).toBeInTheDocument();
	});

	it("renders speakUp when 0.005 < level <= 0.02 and no voice", () => {
		render(<LiveQualityFeedback {...baseProps} peak={0.01} level={0.01} />);
		expect(screen.getByText("⚠ Low volume — speak up")).toBeInTheDocument();
	});

	it("renders the t() sentinel when the i18n mock is active (proves no hardcoded literal)", () => {
		// Flip the flag so the mocked t() returns a sentinel for every
		// key. If the component used a hardcoded literal, the sentinel
		// would NOT appear — the test would fail because getByText
		// wouldn't find "SENTINEL:liveQuality.recording:...".
		// The component renders ``t("microphoneTest.qualityFeedback.recording")``
		// followed by the formatted time. When the sentinel mock is active,
		// ``t()`` returns ``SENTINEL:<key>:<params>`` for EVERY key, so the
		// rendered text is the sentinel for ``microphoneTest.qualityFeedback.recording``
		// (with empty params since the template is a plain string, no
		// interpolation). The time "00:30 / 01:00" is rendered as sibling
		// text nodes within the same <span>, so we match by a regex that
		// allows additional text (the formatted time) around the sentinel.
		useSentinel = true;
		render(<LiveQualityFeedback {...baseProps} />);
		// The sentinel text is rendered as a leading text node inside a
		// <span> that also contains the formatted timer ("00:30 / 01:00").
		// getByText with an exact string fails because the element's full
		// textContent includes the timer. Use a regex that matches the
		// sentinel as a substring of the element's text content.
		expect(
			screen.getByText(
				/SENTINEL:microphoneTest\.qualityFeedback\.recording:\{\}/,
			),
		).toBeInTheDocument();
		// The old hardcoded literal "Recording..." must NOT be present.
		expect(screen.queryByText(/Recording\.\.\./)).toBeNull();
	});

	it("does not render any of the old hardcoded English literals", () => {
		// Belt-and-suspenders: verify the pre-fix English literals are
		// GONE. The old literal used "..." (three dots) instead of "…"
		// (ellipsis), so a regex for three dots must NOT match.
		render(<LiveQualityFeedback {...baseProps} peak={0.95} level={0.001} />);
		expect(screen.queryByText(/Recording\.\.\./)).toBeNull();
		expect(screen.queryByText("Waiting for voice...")).toBeNull();
		expect(screen.queryByText("Voice detected")).toBeNull(); // old had no ✓ prefix literal match
		// The ⚠ prefix is part of the i18n value, so it must appear:
		const clippingEl = screen.getByText(/Volume too high/);
		expect(clippingEl.textContent).toContain("⚠");
	});
});
