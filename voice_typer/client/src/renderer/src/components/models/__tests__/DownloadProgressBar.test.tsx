/**
 * FIX-16 / MDL-12 / A11Y-8: i18n aria-label test for DownloadProgressBar.
 *
 * The pre-fix component used a hardcoded English aria-label
 * ("Model download progress"). After the fix the label comes from the
 * `models.download.progressAria` i18n key, so non-English users get a
 * localized progress-bar announcement from screen readers.
 *
 * ── XA-13 (sub-agent 15) additions ────────────────────────────────────
 * The suite now ALSO covers the four deferred sub-items implemented in
 * this pass:
 *
 *   • XA-13-M2  — throttling boundary coverage (0/5/15/50/95/100).
 *   • XA-13-M5  — explicit error state (role="alert" region + red fill
 *                 + Pause disabled).
 *   • XA-13-M8  — `models.progress.paused` chip rendered when isPaused.
 *   • Priority #3 — Retry button renders iff (error && onRetry) and
 *                 invokes onRetry on click.
 *   • Priority #4 — modelName disambiguates the aria-label.
 *
 * Tests are written to be robust to the new i18n keys being absent
 * from the catalogue (they will be added by the primary agent): they
 * assert on the `error` prop value (which is always surfaced verbatim)
 * and on structural presence/absence, not on the localized label text
 * of the new keys.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is HOISTED before imports, so the mocked `t` is in place by
// the time DownloadProgressBar imports it. We use importOriginal to
// preserve the rest of the i18n module and only override `t` for:
//   • the `models.download.progressAria` sentinel test (existing), AND
//   • the new XA-13 keys that have not yet been added to en.json by
//     the primary agent. The stubs interpolate the params so tests can
//     verify the component passes the right values through. Once the
//     primary agent adds the keys to en.json, the stubs will override
//     the catalogue values during tests — which is fine, since the
//     tests assert on component behaviour, not catalogue contents.
let useSentinel = false;
vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => {
			if (useSentinel && key === "models.download.progressAria") {
				return "SENTINEL_PROGRESS_ARIA";
			}
			switch (key) {
				case "models.download.progressAriaWithName":
					return `${params?.name ?? ""} download: ${params?.percent ?? ""}% complete`;
				case "models.download.errorMessage":
					return `Download failed: ${params?.error ?? ""}`;
				case "models.download.errorMessageWithName":
					return `${params?.name ?? ""} download failed: ${params?.error ?? ""}`;
				case "models.download.retry":
					return "Retry";
				case "models.download.retryAria":
					return "Retry download";
				default:
					return actual.t(key, params);
			}
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

// ─────────────────────────────────────────────────────────────────────
// XA-13-M2: throttling boundary coverage. The pre-existing tests only
// covered progress=42 and 42.7 — both round to 40. The original XA-13
// finding flagged this as insufficient: the throttle formula
// `Math.round(progress / 10) * 10` has surprising behaviour at the 5%
// midpoint (rounds UP to 10) and at the 0% / 100% extremes (no
// rounding needed but the formula must still produce the right value).
// These tests pin the formula at every boundary so a future "round to
// nearest 5" change (or a Math.floor regression) is caught.
// ─────────────────────────────────────────────────────────────────────
describe("DownloadProgressBar — XA-13-M2 (aria-valuenow throttle boundaries)", () => {
	afterEach(() => {
		cleanup();
	});

	it.each([
		{ progress: 0, expected: 0 },
		{ progress: 4, expected: 0 },
		// 5 is the midpoint — Math.round(0.5) === 1 in JS, so 5 → 10.
		{ progress: 5, expected: 10 },
		{ progress: 14.9, expected: 10 },
		{ progress: 15, expected: 20 },
		{ progress: 50, expected: 50 },
		{ progress: 94.9, expected: 90 },
		{ progress: 95, expected: 100 },
		{ progress: 100, expected: 100 },
	])("progress=$progress throttles aria-valuenow to $expected", ({
		progress,
		expected,
	}) => {
		render(<DownloadProgressBar {...baseProps} progress={progress} />);
		const bar = screen.getByRole("progressbar");
		expect(bar).toHaveAttribute("aria-valuenow", String(expected));
	});
});

// ─────────────────────────────────────────────────────────────────────
// XA-13-M5: explicit error state. Before this fix the bar had no error
// UI — a failed download was only surfaced via a toast (which auto-
// dismisses) and the bar was unmounted by the consumer. Now when
// `error` is set the bar: (a) renders the error text in a role="alert"
// region so SR users hear the failure announcement automatically, (b)
// turns the fill red via the `bg-destructive` class, and (c) disables
// the Pause button (pausing a failed download is a no-op).
// ─────────────────────────────────────────────────────────────────────
describe("DownloadProgressBar — XA-13-M5 (explicit error state)", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the error text inside a role=alert region when `error` is set", () => {
		render(
			<DownloadProgressBar
				{...baseProps}
				error="disk full"
				onRetry={vi.fn()}
			/>,
		);
		// role="alert" implicitely creates an aria-live=assertive region.
		const alert = screen.getByRole("alert");
		// The error prop value is always surfaced verbatim, regardless
		// of whether the new `models.download.errorMessage` key has been
		// added to the catalogue yet.
		expect(alert.textContent).toContain("disk full");
	});

	it("switches the status <p> to aria-live=assertive when error is set", () => {
		render(
			<DownloadProgressBar
				{...baseProps}
				error="network timeout"
				onRetry={vi.fn()}
			/>,
		);
		const alert = screen.getByRole("alert");
		expect(alert).toHaveAttribute("aria-live", "assertive");
	});

	it("disables the Pause button when error is set (no-op to pause a failed download)", () => {
		render(
			<DownloadProgressBar
				{...baseProps}
				error="disk full"
				onRetry={vi.fn()}
			/>,
		);
		// The Pause button is the one with the pauseAria label. After
		// the fix it should be disabled.
		const pauseBtn = screen.getByRole("button", {
			name: /pause download/i,
		});
		expect(pauseBtn).toBeDisabled();
	});

	it("does NOT render the paused chip / byte / speed / ETA spans in the error state", () => {
		// The error region should be focused on the failure — not
		// cluttered with stale progress metrics from before the failure.
		render(
			<DownloadProgressBar
				{...baseProps}
				error="disk full"
				onRetry={vi.fn()}
			/>,
		);
		const alert = screen.getByRole("alert");
		// The `·` separator prefixes every supplemental span — none
		// should be present in the error state.
		expect(alert.textContent).not.toContain("·");
	});

	it("falls back to the non-error state when `error` is null", () => {
		// Regression guard: passing `error={null}` must NOT trigger the
		// error UI (the prop is optional, so existing callers that
		// don't pass it at all must keep working).
		render(<DownloadProgressBar {...baseProps} error={null} />);
		expect(screen.queryByRole("alert")).toBeNull();
		// The polite status region is still there.
		const status = screen.getByText("downloading");
		expect(status.closest("p")).toHaveAttribute("aria-live", "polite");
	});

	it("uses the model-specific error message when `modelName` is provided alongside `error`", () => {
		// Priority #4: error messages should be model-specific so users
		// with multiple concurrent downloads know WHICH model failed.
		render(
			<DownloadProgressBar
				{...baseProps}
				modelName="Parakeet TDT"
				error="CUDA out of memory"
				onRetry={vi.fn()}
			/>,
		);
		const alert = screen.getByRole("alert");
		// Both the model name and the error text appear in the alert.
		expect(alert.textContent).toContain("Parakeet TDT");
		expect(alert.textContent).toContain("CUDA out of memory");
	});
});

// ─────────────────────────────────────────────────────────────────────
// XA-13 priority #3: in-place Retry button. Before this fix the only
// recovery path for a failed download was to re-navigate to the model
// card and click Download again — particularly painful for the Parakeet
// case (XA-13-C1) where a multi-GB download fails at 90%+. Now when
// `error` is set AND `onRetry` is provided, a Retry button renders
// next to Cancel.
// ─────────────────────────────────────────────────────────────────────
describe("DownloadProgressBar — XA-13 priority #3 (in-place Retry button)", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders a Retry button when (error && onRetry) are both provided", () => {
		const onRetry = vi.fn();
		render(
			<DownloadProgressBar
				{...baseProps}
				error="disk full"
				onRetry={onRetry}
			/>,
		);
		// The Retry button is identifiable by its aria-label
		// (`models.download.retryAria`). Until the primary agent adds
		// the key, `t()` returns the key itself, so we match loosely.
		const retryBtn = screen.getByRole("button", { name: /retry/i });
		expect(retryBtn).toBeInTheDocument();
	});

	it("clicking the Retry button invokes onRetry exactly once", () => {
		const onRetry = vi.fn();
		render(
			<DownloadProgressBar
				{...baseProps}
				error="disk full"
				onRetry={onRetry}
			/>,
		);
		screen.getByRole("button", { name: /retry/i }).click();
		expect(onRetry).toHaveBeenCalledTimes(1);
	});

	it("does NOT render a Retry button when `error` is set but `onRetry` is absent", () => {
		// The prop is optional — callers that don't want a retry
		// affordance should not see one.
		render(<DownloadProgressBar {...baseProps} error="disk full" />);
		expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
	});

	it("does NOT render a Retry button when `onRetry` is provided but `error` is null", () => {
		// Retry only makes sense in the error state — don't show it
		// during a healthy download.
		render(
			<DownloadProgressBar {...baseProps} error={null} onRetry={vi.fn()} />,
		);
		expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
	});
});

// ─────────────────────────────────────────────────────────────────────
// XA-13-M8: render `models.progress.paused`. The i18n key
// ("· Paused") has existed in en.json since PVT-003 but was never
// rendered — the only paused cue was the amber bar fill, which is
// invisible to SR users and easy to miss for sighted users. The fix
// prepends the chip to the status line when `isPaused` is true.
// ─────────────────────────────────────────────────────────────────────
describe("DownloadProgressBar — XA-13-M8 (render models.progress.paused chip)", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the paused chip when isPaused=true (catalog value: '· Paused')", () => {
		render(<DownloadProgressBar {...baseProps} isPaused={true} />);
		// en.json line 1066: models.progress.paused = "· Paused".
		expect(screen.getByText("· Paused")).toBeInTheDocument();
	});

	it("does NOT render the paused chip when isPaused=false", () => {
		render(<DownloadProgressBar {...baseProps} isPaused={false} />);
		expect(screen.queryByText("· Paused")).toBeNull();
	});

	it("does NOT render the paused chip in the error state even if isPaused=true", () => {
		// The error state overrides the paused state — showing both
		// would be contradictory (the download has failed, not paused).
		render(
			<DownloadProgressBar
				{...baseProps}
				isPaused={true}
				error="disk full"
				onRetry={vi.fn()}
			/>,
		);
		expect(screen.queryByText("· Paused")).toBeNull();
	});
});

// ─────────────────────────────────────────────────────────────────────
// XA-13 priority #4: model-specific aria-label. The pre-fix aria-label
// was always "Model download: N% complete" — useless when two models
// are downloading concurrently (e.g. Whisper + Parakeet on the same
// Models page). When `modelName` is provided the label becomes
// "{name} download: N% complete" so SR users can disambiguate.
// ─────────────────────────────────────────────────────────────────────
describe("DownloadProgressBar — XA-13 priority #4 (model-specific aria-label)", () => {
	afterEach(() => {
		cleanup();
	});

	it("includes the model name in the aria-label when `modelName` is provided", () => {
		render(
			<DownloadProgressBar
				{...baseProps}
				progress={50}
				modelName="Parakeet TDT"
			/>,
		);
		const bar = screen.getByRole("progressbar");
		const label = bar.getAttribute("aria-label") ?? "";
		// The model name must appear in the label (priority #4).
		expect(label).toContain("Parakeet TDT");
		// The percent must still be interpolated (regression guard
		// against the new key dropping the {percent} placeholder).
		expect(label).toContain("50");
	});

	it("uses the generic aria-label when `modelName` is NOT provided (backwards compat)", () => {
		// Existing callers (LocalModelsPanel) don't yet pass modelName
		// — they must keep seeing the original "Model download: N%
		// complete" label.
		render(<DownloadProgressBar {...baseProps} progress={50} />);
		const bar = screen.getByRole("progressbar");
		expect(bar).toHaveAttribute("aria-label", "Model download: 50% complete");
	});

	it("the model-specific label differs from the generic label", () => {
		// Structural assertion: providing modelName MUST change the
		// aria-label (proves modelName is not silently ignored).
		const { rerender } = render(
			<DownloadProgressBar {...baseProps} progress={50} />,
		);
		const genericLabel = screen
			.getByRole("progressbar")
			.getAttribute("aria-label");

		rerender(
			<DownloadProgressBar
				{...baseProps}
				progress={50}
				modelName="Whisper small.en"
			/>,
		);
		const namedLabel = screen
			.getByRole("progressbar")
			.getAttribute("aria-label");

		expect(namedLabel).not.toEqual(genericLabel);
		expect(namedLabel).toContain("Whisper small.en");
	});
});
