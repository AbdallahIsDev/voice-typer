/**
 * History page — display-cap footer count wiring.
 *
 * The cap footer (rendered once the visible window reaches BOTH the
 * 200-row display cap and the end of the loaded cache while the backend
 * still reports more) used to interpolate the literal placeholder
 * `total: "N+"` (and a hardcoded `shown: "200"`) into the
 * `history.showingCap` template — every locale rendered "Showing 200 of
 * N+". The fix wires the TRUE all-time row count via the existing
 * `get_history_count` IPC (the same consumption the Analytics page's
 * useDashboardData uses) and derives `shown` from HISTORY_DISPLAY_CAP:
 * "Showing 200 of 1482 — use search to find older".
 *
 * While the count is still loading (or if its fetch fails) the footer
 * degrades to an ellipsis placeholder — it must NEVER render "N+".
 *
 * Mock strategy mirrors History.test.tsx (stableMocks preamble +
 * per-test dynamic page import + module-cache reset). The rows fixture
 * pages 50 rows per get_history call so three "Load More" clicks grow
 * the cache to the 200-row display cap exactly like the real flow.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	lastUpdatedMock,
	navigationMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	snackbarMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useSnackbar", () => snackbarMock());
vi.mock("@/hooks/useLastUpdated", () => lastUpdatedMock({ withRefresh: true }));
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { useGlobalSearch } from "@/hooks/useGlobalSearch";
import { t } from "@/i18n/i18n";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

/** Build the paged rows fixture: `total` records, 50 per page. */
function makeRecords(total: number): HistoryRecord[] {
	const records: HistoryRecord[] = [];
	for (let i = 0; i < total; i++) {
		records.push({
			id: i + 1,
			text: `entry ${i + 1}`,
			timestamp: new Date(Date.now() - i * 60_000).toISOString(),
			duration: 1,
			model: "tiny",
			device: "cpu",
			word_count: 2,
			char_count: 8,
			favorite: 0,
			language: "en",
		});
	}
	return records;
}

const ALL_RECORDS = makeRecords(250);
const zeroStats: TodayStats = {
	count: 0,
	chars: 0,
	word_count: 0,
	duration: 0,
};

/** Serve get_history as 50-row pages (ignores the cursor — same rows by
 *  offset, like the backend's OFFSET fallback). */
function pageRows(
	payload: Record<string, unknown> | undefined,
): HistoryRecord[] {
	const offset = typeof payload?.offset === "number" ? payload.offset : 0;
	return ALL_RECORDS.slice(offset, offset + 50);
}

beforeEach(() => {
	resetStableMocks();
	localStorage.clear();
	useGlobalSearch.setState({ query: "" });
	vi.resetModules();
	vi.doMock("@/pages/history/hooks/useHistoryCache", async () => {
		const actual = await vi.importActual<
			typeof import("@/pages/history/hooks/useHistoryCache")
		>("@/pages/history/hooks/useHistoryCache");
		return { ...actual };
	});
});

afterEach(() => {
	cleanup();
	(window as unknown as { window_?: unknown }).window_ = undefined;
});

/** Render the page, then click "Load More" until the cap footer replaces
 *  the button (initial page + 3 widenings = 200 rows). */
async function renderToCapFooter(): Promise<void> {
	mockCall.mockImplementation(
		(type: string, payload?: Record<string, unknown>) => {
			if (type === "get_history") return Promise.resolve(pageRows(payload));
			if (type === "get_today_stats") return Promise.resolve(zeroStats);
			if (type === "get_history_count") return Promise.resolve({ count: 1482 });
			return Promise.resolve({});
		},
	);

	const { default: HistoryPage } = await import("@/pages/History");
	render(<HistoryPage />);

	// Wait for the first page to render, then widen three times. After
	// every load the button returns enabled; the third click's load makes
	// records.length (200) hit the cap with visibleCount caught up, so
	// the footer replaces the button.
	await waitFor(() => {
		expect(screen.getByText("entry 1")).toBeTruthy();
	});
	for (let click = 0; click < 3; click++) {
		const loadMore = await waitFor(() =>
			screen.getByRole("button", { name: t("history.loadMore") }),
		);
		fireEvent.click(loadMore);
	}
}

describe("History display-cap footer count", () => {
	it("renders the real total from get_history_count (no N+ placeholder)", async () => {
		await renderToCapFooter();

		const expected = t("history.showingCap", { shown: "200", total: "1482" });
		await waitFor(() => {
			expect(screen.getByText(expected)).toBeTruthy();
		});
		// The wiring actually fetches the count over the existing IPC.
		expect(
			mockCall.mock.calls.some(
				(args: unknown[]) => args[0] === "get_history_count",
			),
		).toBe(true);
	});

	it("renders the ellipsis placeholder (never N+) while the count is loading", async () => {
		mockCall.mockImplementation(
			(type: string, payload?: Record<string, unknown>) => {
				if (type === "get_history") return Promise.resolve(pageRows(payload));
				if (type === "get_today_stats") return Promise.resolve(zeroStats);
				// Never resolves — the footer must degrade gracefully.
				if (type === "get_history_count") return new Promise(() => {});
				return Promise.resolve({});
			},
		);

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);
		await waitFor(() => {
			expect(screen.getByText("entry 1")).toBeTruthy();
		});
		for (let click = 0; click < 3; click++) {
			const loadMore = await waitFor(() =>
				screen.getByRole("button", { name: t("history.loadMore") }),
			);
			fireEvent.click(loadMore);
		}

		const expected = t("history.showingCap", { shown: "200", total: "…" });
		await waitFor(() => {
			expect(screen.getByText(expected)).toBeTruthy();
		});
		expect(screen.queryByText(/N\+/)).toBeNull();
	});

	it("renders the ellipsis placeholder (never N+) when the count fetch fails", async () => {
		mockCall.mockImplementation(
			(type: string, payload?: Record<string, unknown>) => {
				if (type === "get_history") return Promise.resolve(pageRows(payload));
				if (type === "get_today_stats") return Promise.resolve(zeroStats);
				if (type === "get_history_count")
					return Promise.reject(new Error("backend unreachable"));
				return Promise.resolve({});
			},
		);

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);
		await waitFor(() => {
			expect(screen.getByText("entry 1")).toBeTruthy();
		});
		for (let click = 0; click < 3; click++) {
			const loadMore = await waitFor(() =>
				screen.getByRole("button", { name: t("history.loadMore") }),
			);
			fireEvent.click(loadMore);
		}

		const expected = t("history.showingCap", { shown: "200", total: "…" });
		await waitFor(() => {
			expect(screen.getByText(expected)).toBeTruthy();
		});
		expect(screen.queryByText(/N\+/)).toBeNull();
	});
});
