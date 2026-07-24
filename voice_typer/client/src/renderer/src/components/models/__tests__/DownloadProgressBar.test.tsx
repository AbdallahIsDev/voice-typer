/**
 * FIX-16 / MDL-12 / A11Y-8: i18n aria-label test for DownloadProgressBar.
 *
 * The pre-fix component used a hardcoded English aria-label
 * ("Model download progress"). After the fix the label comes from the
 * `models.download.progressAria` i18n key, so non-English users get a
 * localized progress-bar announcement from screen readers.
 *
 * The test verifies:
 *   1. The progressbar role is preserved.
 *   2. The aria-label matches the en.json catalog value
 *      `models.download.progressAria` with the {percent} placeholder
 *      interpolated ("Model download: 42% complete" for progress=42).
 *   3. Mocking `t()` to return a sentinel makes the aria-label flip to
 *      the sentinel — proving the label is NOT a hardcoded literal.
 *   4. The aria-valuenow/min/max attributes still reflect the `progress`
 *      prop (regression guard for the existing behavior).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is HOISTED before imports, so the mocked `t` is in place by
// the time DownloadProgressBar imports it. We use importOriginal to
// preserve the rest of the i18n module and only override `t` when the
// `useSentinel` flag is set.
let useSentinel = false;
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			if (useSentinel && key === "models.download.progressAria") {
				return "SENTINEL_PROGRESS_ARIA";
			}
			return actual.t(key, params);
		},
	};
});

import { DownloadProgressBar } from "@/components/models/DownloadProgressBar";

const baseProps = {
	progress: 42,
	status: "downloading",
	isPaused: false,
	downloadedBytes: 1024 * 500,
	totalBytes: 1024 * 1024,
	speedBps: 1024 * 100,
	etaSeconds: 60,
	onTogglePause: vi.fn(),
	onCancel: vi.fn(),
};

describe("DownloadProgressBar — MDL-12 / A11Y-8 (i18n aria-label)", () => {
	afterEach(() => {
		cleanup();
		useSentinel = false;
	});

	beforeEach(() => {
		useSentinel = false;
	});

	it("renders a progressbar with role + aria-valuenow/min/max (preserved behavior)", () => {
		render(<DownloadProgressBar {...baseProps} />);
		const bar = screen.getByRole("progressbar");
		expect(bar).toBeInTheDocument();
		expect(bar).toHaveAttribute("aria-valuemin", "0");
		expect(bar).toHaveAttribute("aria-valuemax", "100");
		// NF-R15-17: aria-valuenow is throttled to the nearest 10% so
		// screen readers don't broadcast a stream of percentage updates
		// every frame. 42 → 40.
		expect(bar).toHaveAttribute("aria-valuenow", "40");
	});

	it("aria-label is sourced from models.download.progressAria with {percent} interpolated", () => {
		render(<DownloadProgressBar {...baseProps} />);
		const bar = screen.getByRole("progressbar");
		// en.json: models.download.progressAria = "Model download: {percent}% complete".
		// BG-4: the {percent} placeholder MUST be interpolated — screen
		// readers would otherwise announce the literal token "{percent}"
		// (with curly braces) for the entire duration of every download.
		// For progress=42 the expected label is "Model download: 42% complete".
		expect(bar).toHaveAttribute("aria-label", "Model download: 42% complete");
		expect(bar.getAttribute("aria-label")).not.toContain("{percent}");
	});

	it("aria-label flips to the sentinel when t() is mocked (proves no hardcoded literal)", () => {
		useSentinel = true;
		render(<DownloadProgressBar {...baseProps} />);
		const bar = screen.getByRole("progressbar");
		expect(bar).toHaveAttribute("aria-label", "SENTINEL_PROGRESS_ARIA");
	});

	it("status <p> is an aria-live=polite region (BG-75: announce download status changes to SR users)", () => {
		render(<DownloadProgressBar {...baseProps} />);
		// The status line shows the human-readable status string + the
		// downloaded/total/speed/ETA spans. SR users need to hear updates
		// as the download progresses — wrap the <p> in aria-live=polite.
		const status = screen.getByText("downloading");
		expect(status.closest("p")).toHaveAttribute("aria-live", "polite");
	});

	it("rounds the aria-valuenow to the nearest 10 (throttled for SR)", () => {
		// NF-R15-17: aria-valuenow is throttled to the nearest 10% so
		// screen readers don't broadcast a stream of percentage updates
		// every frame. 42.7 → 40 (Math.round(42.7 / 10) * 10 === 40).
		render(<DownloadProgressBar {...baseProps} progress={42.7} />);
		const bar = screen.getByRole("progressbar");
		expect(bar).toHaveAttribute("aria-valuenow", "40");
	});
});
