/**
 * ActivityList — grouped-by-date rendering + click-to-expand rows.
 *
 * Covers the History page's list behaviors (the Home page uses the same
 * component flat, without the new props, and must be unaffected):
 *
 *   1. ``groupByDate`` renders one section per local calendar day with a
 *      localized heading, and rows show only their TIME (no repeated
 *      full date).
 *   2. Rows carrying the 500-char preview (``text_truncated``) become
 *      click-to-expand: first expansion fetches the FULL text via
 *      ``onFetchFullText`` (the same row also copies the displayed
 *      text, so an expanded row copies the full transcript).
 *   3. The text block is keyboard-operable (Enter / Space) and exposes
 *      the disclosure state via ``aria-expanded``.
 *   4. Short, non-truncated rows stay INERT — no button role, no hover
 *      affordance (hover states only where a genuine click action
 *      exists).
 */

import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	resetStableMocks,
	sonnerMock,
} from "@/__tests__/helpers/stableMocks";

vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());

import { toast } from "sonner";
import ActivityList from "@/components/dashboard/ActivityList";
import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

function rec(
	id: number,
	timestamp: string,
	overrides: Partial<HistoryRecord> = {},
): HistoryRecord {
	return {
		id,
		text: `entry ${id}`,
		timestamp,
		duration: 1,
		model: "tiny",
		device: "cpu",
		word_count: 2,
		char_count: 9,
		favorite: 0,
		language: "en",
		...overrides,
	};
}

function localIso(offsetDays: number, hour: number): string {
	const d = new Date();
	d.setDate(d.getDate() - offsetDays);
	d.setHours(hour, 0, 0, 0);
	return d.toISOString();
}

/** 500-char preview + truncated flag (the backend list shape). */
function truncatedRec(id: number, timestamp: string): HistoryRecord {
	return rec(id, timestamp, {
		text: "word ".repeat(100).trim(), // 500 chars
		char_count: 1200,
		text_truncated: true,
		text_full_length: 1200,
	});
}

describe("ActivityList date grouping", () => {
	afterEach(() => {
		cleanup();
		resetStableMocks();
	});

	it("renders one section per local day with localized headings; rows show time only", () => {
		const items = [
			rec(1, localIso(0, 12)),
			rec(2, localIso(0, 5)),
			rec(3, localIso(1, 8)),
		];
		render(<ActivityList items={items} lineClamp={3} groupByDate />);

		// Section headers: today + yesterday (analytics.* keys).
		expect(screen.getByText("Today")).toBeTruthy();
		expect(screen.getByText("Yesterday")).toBeTruthy();

		// Rows show only time-of-day — the FULL "Mon 3 · 12:00 PM" style
		// date+time line must NOT appear in grouped mode. The legacy
		// formatter renders `MMM D · h:mm AM/PM`; assert no " · " date
		// separator survives in any row meta line.
		const metas = screen.getAllByText(/·/);
		for (const meta of metas) {
			// Each meta is "HH:MM AM/PM · 2 words" — at most ONE separator
			// (before the word count), never a date segment.
			const segments = meta.textContent?.split("·").length ?? 0;
			expect(segments).toBe(2);
		}
	});

	it("flat mode (no groupByDate) keeps the legacy date+time line and no headers", () => {
		render(<ActivityList items={[rec(1, localIso(0, 12))]} lineClamp={2} />);
		expect(screen.queryByText("Today")).toBeNull();
		expect(screen.queryByText("Yesterday")).toBeNull();
		const meta = screen.getAllByText(/·/)[0];
		expect(meta).toBeDefined();
		// Legacy line: "MMM D · HH:MM · 2 words" → three segments.
		expect(meta?.textContent?.split("·").length).toBe(3);
	});

	it("hideHeader suppresses the title row (History renders under its own page heading)", () => {
		render(<ActivityList items={[rec(1, localIso(0, 12))]} hideHeader />);
		expect(screen.queryByText(t("home.recentActivity"))).toBeNull();
	});
});

describe("ActivityList click-to-expand rows", () => {
	afterEach(() => {
		cleanup();
		resetStableMocks();
		vi.unstubAllGlobals();
	});

	it("expandable rows: short inert text has NO button role or hover affordance", () => {
		const { container } = render(
			<ActivityList
				items={[rec(1, localIso(0, 12), { text: "hello world" })]}
				onFetchFullText={vi.fn()}
			/>,
		);
		// No role="button" anywhere — the text block is inert.
		expect(container.querySelector('[role="button"]')).toBeNull();
		// No hover/cursor classes on the TEXT container itself (the
		// app-wide rule: hover affordances only where a genuine click
		// action exists — the copy Button legitimately keeps its own).
		const textContainer = container.querySelector("p")?.parentElement;
		expect(textContainer?.className).not.toContain("cursor-pointer");
		expect(textContainer?.className).not.toContain("hover:");
	});

	it("truncated row expands on click, fetches full text once, and collapses via Show less", async () => {
		const fullText = "THE FULL TRANSCRIPT ".repeat(30);
		const fetchFullText = vi.fn().mockResolvedValue(fullText);
		render(
			<ActivityList
				items={[truncatedRec(7, localIso(0, 12))]}
				onFetchFullText={fetchFullText}
			/>,
		);

		// The text block is the disclosure control, collapsed initially.
		const textBtn = screen.getByTestId("activity-row-text-toggle");
		expect(textBtn.getAttribute("aria-expanded")).toBe("false");

		fireEvent.click(textBtn);

		// Full text fetched with the record id and rendered unclamped.
		await waitFor(() => {
			expect(fetchFullText).toHaveBeenCalledWith(7);
			expect(screen.getByText(/THE FULL TRANSCRIPT/)).toBeTruthy();
		});
		const expanded = screen.getByTestId("activity-row-text-toggle");
		expect(expanded.getAttribute("aria-expanded")).toBe("true");

		// Show less collapses again.
		fireEvent.click(screen.getByText(t("home.showLess")));
		expect(
			screen
				.getByTestId("activity-row-text-toggle")
				.getAttribute("aria-expanded"),
		).toBe("false");

		// Second expansion must NOT re-fetch (full text cached in-row).
		fireEvent.click(screen.getByTestId("activity-row-text-toggle"));
		await waitFor(() => {
			expect(
				screen
					.getByTestId("activity-row-text-toggle")
					.getAttribute("aria-expanded"),
			).toBe("true");
		});
		expect(fetchFullText).toHaveBeenCalledTimes(1);
	});

	it("keyboard operable: Enter and Space toggle the expanded state", async () => {
		const fetchFullText = vi.fn().mockResolvedValue("FULL KEYBOARD TEXT");
		render(
			<ActivityList
				items={[truncatedRec(9, localIso(0, 12))]}
				onFetchFullText={fetchFullText}
			/>,
		);
		const textBtn = screen.getByTestId("activity-row-text-toggle");
		expect(textBtn.getAttribute("aria-expanded")).toBe("false");

		fireEvent.keyDown(textBtn, { key: "Enter" });
		await waitFor(() => {
			expect(
				screen
					.getByTestId("activity-row-text-toggle")
					.getAttribute("aria-expanded"),
			).toBe("true");
		});

		// Collapse again via Space on the still-focusable text block.
		fireEvent.keyDown(screen.getByTestId("activity-row-text-toggle"), {
			key: " ",
		});
		await waitFor(() => {
			expect(
				screen
					.getByTestId("activity-row-text-toggle")
					.getAttribute("aria-expanded"),
			).toBe("false");
		});
	});

	it("fetch failure toasts an error and keeps the row collapsed on the preview text", async () => {
		const fetchFullText = vi.fn().mockRejectedValue(new Error("boom"));
		render(
			<ActivityList
				items={[truncatedRec(3, localIso(0, 12))]}
				onFetchFullText={fetchFullText}
			/>,
		);
		fireEvent.click(screen.getByTestId("activity-row-text-toggle"));
		await waitFor(() => {
			expect(toast.error).toHaveBeenCalledWith(
				t("activityList.loadTextFailed"),
			);
		});
		// The row stays collapsed — no phantom expanded state.
		expect(
			screen
				.getByTestId("activity-row-text-toggle")
				.getAttribute("aria-expanded"),
		).toBe("false");
	});

	it("empty full-text sentinel (row deleted mid-flight) toasts instead of expanding", async () => {
		const fetchFullText = vi.fn().mockResolvedValue("");
		render(
			<ActivityList
				items={[truncatedRec(4, localIso(0, 12))]}
				onFetchFullText={fetchFullText}
			/>,
		);
		fireEvent.click(screen.getByTestId("activity-row-text-toggle"));
		await waitFor(() => {
			expect(toast.error).toHaveBeenCalledWith(
				t("activityList.loadTextFailed"),
			);
		});
		expect(
			screen
				.getByTestId("activity-row-text-toggle")
				.getAttribute("aria-expanded"),
		).toBe("false");
	});

	it("copy uses the DISPLAYED text — expanded rows copy the full transcript", async () => {
		const writeText = vi.fn().mockResolvedValue(undefined);
		Object.defineProperty(navigator, "clipboard", {
			value: { writeText },
			configurable: true,
		});
		const fullText = "FULL COPY TARGET TEXT ".repeat(20);
		render(
			<ActivityList
				items={[truncatedRec(5, localIso(0, 12))]}
				onFetchFullText={vi.fn().mockResolvedValue(fullText)}
			/>,
		);

		// Copy while collapsed copies the preview text.
		fireEvent.click(
			screen.getByRole("button", { name: t("history.copyText") }),
		);
		await waitFor(() => {
			expect(writeText).toHaveBeenCalledWith(expect.stringContaining("word"));
		});
		expect(writeText.mock.calls[0]?.[0]).not.toContain("FULL COPY TARGET");

		// Expand, then copy — must carry the full transcript.
		fireEvent.click(screen.getByTestId("activity-row-text-toggle"));
		await waitFor(() => {
			expect(
				screen
					.getByTestId("activity-row-text-toggle")
					.getAttribute("aria-expanded"),
			).toBe("true");
		});
		fireEvent.click(
			screen.getByRole("button", { name: t("history.copyText") }),
		);
		await waitFor(() => {
			expect(writeText).toHaveBeenLastCalledWith(fullText);
		});
	});

	it("action button clicks never toggle the text block", async () => {
		const onToggleFavorite = vi.fn();
		render(
			<ActivityList
				items={[truncatedRec(6, localIso(0, 12))]}
				onFetchFullText={vi.fn().mockResolvedValue("FULL")}
				onToggleFavorite={onToggleFavorite}
			/>,
		);
		fireEvent.click(
			screen.getByRole("button", {
				name: t("activityList.addToFavorites"),
			}),
		);
		expect(onToggleFavorite).toHaveBeenCalledWith(6);
		// The text block stayed collapsed (the click did not bubble into
		// an expansion).
		expect(
			screen
				.getByTestId("activity-row-text-toggle")
				.getAttribute("aria-expanded"),
		).toBe("false");
	});
});
