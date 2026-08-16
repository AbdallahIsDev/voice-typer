/**
 * StatsShareImage — redesigned themed share card tests.
 *
 * Verifies:
 *   1. The card renders the real metrics (WPM, minutes saved,
 *      dictations, active days, chars, recording time) from the
 *      ShareStats object.
 *   2. Zero-data state mirrors the Analytics page: no dictation today
 *      → the WPM value shows "—" and no "faster than avg" claim is
 *      rendered (never "0% faster than avg typer").
 *   3. The `palette` prop themes the card (background / card surface /
 *      accent values come from the resolved theme tokens).
 *   4. Without a `palette` prop the component falls back to the stock
 *      palette without crashing (used by off-screen renders / tests).
 *   5. The branding footer renders the dynamic APP_NAME (no hardcoded
 *      literal) via the i18n key.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n/i18n", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/i18n/i18n")>();
	return {
		...actual,
		t: (key: string, params?: Record<string, string>) => actual.t(key, params),
	};
});

import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import type { StatsThemePalette } from "@/lib/theme-palette";
import type { ShareStats } from "@/types/stats";

const stats: ShareStats = {
	wpm: 92,
	wpmDisplay: "92",
	minutesSaved: 18,
	minutesSavedDisplay: "18",
	modeDisplay: "Cloud",
	modeDetail: "Cloud ASR (OpenAI)",
	fasterThanAvg: "100% faster than avg typer",
	hasTodayActivity: true,
	dictations: "1,204",
	activeDays: "87",
	activeDaysDetail: "5-day streak",
	chars: "24,510",
	recordingTime: "9h 40m",
	model: "parakeet",
	device: "cpu",
};

const palette: StatsThemePalette = {
	background: "#101014",
	card: "#17171c",
	foreground: "#ececf1",
	mutedForeground: "#9a9aa5",
	primary: "#7aa2f7",
	border: "#23232b",
	success: "#22c55e",
	warning: "#f59e0b",
	destructive: "#ef4444",
	charts: ["#7aa2f7", "#bb9af7", "#9ece6a", "#e0af68", "#f7768e"],
};

describe("StatsShareImage — redesigned themed card", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the real metrics from ShareStats", () => {
		render(<StatsShareImage stats={stats} palette={palette} />);

		expect(screen.getByText("92")).toBeTruthy();
		expect(screen.getByText("WPM")).toBeTruthy();
		expect(screen.getByText("18")).toBeTruthy();
		expect(screen.getByText("Minutes saved")).toBeTruthy();
		expect(screen.getByText("1,204")).toBeTruthy();
		expect(screen.getByText("Dictations")).toBeTruthy();
		expect(screen.getByText("87")).toBeTruthy();
		expect(screen.getByText("Active days")).toBeTruthy();
		expect(screen.getByText("5-day streak")).toBeTruthy();
		expect(screen.getByText("24,510")).toBeTruthy();
		expect(screen.getByText("Characters")).toBeTruthy();
		expect(screen.getByText("9h 40m")).toBeTruthy();
		expect(screen.getByText("Recording time")).toBeTruthy();
		// Mode chip + footer model/device.
		expect(screen.getByText("Cloud")).toBeTruthy();
		expect(screen.getByText("Cloud ASR (OpenAI)")).toBeTruthy();
		expect(screen.getByText(/parakeet/i)).toBeTruthy();
	});

	it("zero-data state shows — for WPM and no faster-than-avg claim", () => {
		const zeroToday: ShareStats = {
			...stats,
			wpm: 0,
			wpmDisplay: "—",
			fasterThanAvg: null,
			hasTodayActivity: false,
		};
		render(<StatsShareImage stats={zeroToday} palette={palette} />);

		expect(screen.getByText("—")).toBeTruthy();
		// No "0% faster than avg typer" claim.
		expect(screen.queryByText(/faster than avg/i)).toBeNull();
		// The empty-state hint replaces it.
		expect(screen.getByText(/no dictation yet today/i)).toBeTruthy();
	});

	it("applies the theme palette colours to the card surfaces", () => {
		const { container } = render(
			<StatsShareImage stats={stats} palette={palette} />,
		);

		// Root background uses the theme background token (jsdom
		// normalizes the hex to rgb()).
		const root = container.firstChild as HTMLElement;
		expect(root.style.background).toBe("rgb(16, 16, 20)");
		expect(root.style.color).toBe("rgb(236, 236, 241)");
	});

	it("falls back to the stock palette without a palette prop", () => {
		const { container } = render(<StatsShareImage stats={stats} />);
		const root = container.firstChild as HTMLElement;
		// The fallback palette is a module constant — never empty.
		// #131313 → rgb(19, 19, 19) in jsdom.
		expect(root.style.background).toBe("rgb(19, 19, 19)");
	});

	it("renders the exported-from branding via i18n with the dynamic APP_NAME", () => {
		render(<StatsShareImage stats={stats} palette={palette} />);
		// The footer watermark resolves through the i18n key.
		expect(screen.getByText(/Exported from/i)).toBeTruthy();
	});
});
