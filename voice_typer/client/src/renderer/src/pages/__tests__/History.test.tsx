/**
 * History page tests — , ,  regression coverage.
 *
 * : Favorites toggle exposes aria-pressed + a stable accessible
 *        name containing the visible "Favorites" text (Label-in-Name).
 *
 * : doExport honours the active search/favorites filter —
 *        branches on searchQuery / favoritesOnly to call search_history
 *        / get_favorites (with paging) instead of always calling
 *        get_history. Fires an info toast when exporting a filtered
 *        subset.
 *
 * : handleClearAll is unambiguous under an active filter —
 *        skips the `records.length === 0` short-circuit using the
 *        cached stats count (visible list may be empty while the
 *        total is not) and shows a clearer confirmation message that
 *        ALL history (including hidden entries) will be deleted.
 *
 * Mock strategy mirrors Home.test.tsx — usePython is mocked so the
 * cache hook's IPC calls are intercepted by `mockCall`, and
 * usePythonEvent is a no-op (the page keeps its debounce wrapper +
 * usePythonEvent calls for the R7-F13 source-level contract).
 *
 *  export tests exercise the `useHistoryExport` hook directly via
 * `renderHook` because the Radix DropdownMenu used by ExportFormatMenu
 * requires real pointer events (fireEvent.click alone doesn't open the
 * menu in the jsdom test environment — a known limitation shared with
 * the pre-existing rw1 "History export null-safe" test).
 */
import {
	cleanup,
	fireEvent,
	render,
	renderHook,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: vi.fn() }),
	showUndoableToast: vi.fn(),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: "",
		markUpdated: vi.fn(),
		refreshing: false,
		withRefresh: async <T,>(op: () => Promise<T>): Promise<T> => op(),
	}),
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: vi.fn() }),
}));

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

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
		warning: vi.fn(),
		info: vi.fn(),
		dismiss: vi.fn(),
	},
	Toaster: () => null,
}));

vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

import { toast } from "sonner";
import { t } from "@/i18n/i18n";
import type { HistoryRecord, TodayStats, WindowBridge } from "@/types/ipc";

const sampleRecord = (
	overrides: Partial<HistoryRecord> = {},
): HistoryRecord => ({
	id: 1,
	text: "hello world",
	timestamp: new Date().toISOString(),
	duration: 1,
	model: "tiny",
	device: "cpu",
	word_count: 2,
	char_count: 11,
	favorite: 0,
	language: "en",
	...overrides,
});

const zeroStats: TodayStats = {
	count: 0,
	chars: 0,
	word_count: 0,
	duration: 0,
};

beforeEach(() => {
	mockCall.mockReset();
	mockPythonEvent.mockReset();
	localStorage.clear();
	vi.resetModules();
	// Reset the module-level cache so tests don't leak state.
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

//

describe("BG-51: Favorites toggle exposes aria-pressed + stable accessible name", () => {
	it("renders the Favorites button with aria-pressed=false initially", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") return Promise.resolve([sampleRecord()]);
			if (type === "get_today_stats") return Promise.resolve(zeroStats);
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);

		await waitFor(() => {
			expect(screen.getByText("hello world")).toBeTruthy();
		});

		const favBtn = screen.getByRole("button", { name: t("history.favorites") });
		expect(favBtn).toBeTruthy();
		//aria-pressed conveys the toggle state.
		expect(favBtn.getAttribute("aria-pressed")).toBe("false");
		//the accessible name matches the visible label (Label-in-Name).
		expect(favBtn.textContent?.trim()).toBe(t("history.favorites"));
	});

	it("toggles aria-pressed to true when the favorites filter is activated", async () => {
		// When favoritesOnly is toggled on, load() calls get_favorites.
		// Return an empty list so the EmptyState renders without errors.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") return Promise.resolve([sampleRecord()]);
			if (type === "get_favorites") return Promise.resolve([]);
			if (type === "get_today_stats") return Promise.resolve(zeroStats);
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);

		await waitFor(() => {
			expect(screen.getByText("hello world")).toBeTruthy();
		});

		const favBtn = screen.getByRole("button", { name: t("history.favorites") });
		expect(favBtn.getAttribute("aria-pressed")).toBe("false");

		fireEvent.click(favBtn);

		// After click, the favorites filter is active — aria-pressed flips
		// to "true" and the accessible name stays "Favorites" (no swap).
		await waitFor(() => {
			expect(favBtn.getAttribute("aria-pressed")).toBe("true");
		});
		expect(favBtn.textContent?.trim()).toBe(t("history.favorites"));
	});
});

//

describe("BG-52: doExport honours active search/favorites filter", () => {
	it("calls get_history when no filter is active (original behavior)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") return Promise.resolve([sampleRecord()]);
			return Promise.resolve({});
		});

		const exportHistory = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/history.json" });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportHistory,
		};

		//doExport is extracted to useHistoryExport. We exercise
		// the hook directly so the test doesn't depend on Radix menu
		// pointer-event behaviour (which jsdom doesn't fully simulate).
		const { useHistoryExport } = await import(
			"@/pages/history/hooks/useHistoryExport"
		);
		const { result } = renderHook(() =>
			useHistoryExport({
				call: mockCall,
				records: [sampleRecord()],
				sortOrder: "newest",
				searchQuery: "",
				favoritesOnly: false,
			}),
		);

		await result.current.doExport("json");

		const getHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		);
		const searchHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "search_history",
		);
		const getFavoritesCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_favorites",
		);
		// get_history is called (export paging).
		expect(getHistoryCalls.length).toBeGreaterThanOrEqual(1);
		// search_history + get_favorites are NEVER called without an active filter.
		expect(searchHistoryCalls.length).toBe(0);
		expect(getFavoritesCalls.length).toBe(0);
		// The Electron bridge was invoked.
		expect(exportHistory).toHaveBeenCalledTimes(1);
		// No filter toast fires when no filter is active.
		expect(toast.info).not.toHaveBeenCalled();
	});

	it("calls search_history when a search query is active", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "search_history")
				return Promise.resolve([sampleRecord({ text: "hello match" })]);
			return Promise.resolve({});
		});

		const exportHistory = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/history.json" });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportHistory,
		};

		const { useHistoryExport } = await import(
			"@/pages/history/hooks/useHistoryExport"
		);
		const { result } = renderHook(() =>
			useHistoryExport({
				call: mockCall,
				records: [sampleRecord({ text: "hello match" })],
				sortOrder: "newest",
				searchQuery: "hello",
				favoritesOnly: false,
			}),
		);

		await result.current.doExport("json");

		const searchHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "search_history",
		);
		const getHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		);
		//search_history is called (export paging under the
		// active search filter), get_history is NOT.
		expect(searchHistoryCalls.length).toBeGreaterThanOrEqual(1);
		expect(getHistoryCalls.length).toBe(0);
		expect(exportHistory).toHaveBeenCalledTimes(1);
	});

	it("calls get_favorites when favorites filter is active", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_favorites")
				return Promise.resolve([
					sampleRecord({ text: "favorite entry", favorite: 1 }),
				]);
			return Promise.resolve({});
		});

		const exportHistory = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/history.json" });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportHistory,
		};

		const { useHistoryExport } = await import(
			"@/pages/history/hooks/useHistoryExport"
		);
		const { result } = renderHook(() =>
			useHistoryExport({
				call: mockCall,
				records: [sampleRecord({ text: "favorite entry", favorite: 1 })],
				sortOrder: "newest",
				searchQuery: "",
				favoritesOnly: true,
			}),
		);

		await result.current.doExport("json");

		const getFavoritesCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_favorites",
		);
		const getHistoryCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_history",
		);
		//get_favorites is called (export paging under the
		// active favorites filter), get_history is NOT.
		expect(getFavoritesCalls.length).toBeGreaterThanOrEqual(1);
		expect(getHistoryCalls.length).toBe(0);
		expect(exportHistory).toHaveBeenCalledTimes(1);
	});

	it("fires an info toast when exporting a filtered subset", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "search_history")
				return Promise.resolve([sampleRecord({ text: "hello match" })]);
			return Promise.resolve({});
		});

		const exportHistory = vi
			.fn()
			.mockResolvedValue({ success: true, path: "/tmp/history.json" });
		(window as unknown as { window_: Partial<WindowBridge> }).window_ = {
			exportHistory,
		};

		const { useHistoryExport } = await import(
			"@/pages/history/hooks/useHistoryExport"
		);
		const { result } = renderHook(() =>
			useHistoryExport({
				call: mockCall,
				records: [sampleRecord({ text: "hello match" })],
				sortOrder: "newest",
				searchQuery: "hello",
				favoritesOnly: false,
			}),
		);

		await result.current.doExport("json");

		//filtered export fires an info toast so the user knows the
		// exported file reflects the active filter (not the full history).
		expect(toast.info).toHaveBeenCalledWith(t("history.exportFilteredToast"));
	});
});

//

describe("BG-53: Clear All under active filter is unambiguous", () => {
	it("shows the filter-aware confirmation message when a filter is active", async () => {
		// Visible records: empty (favorites filter active). Total stats: 5.
		//the short-circuit must NOT fire here (totalCount > 0),
		// and the confirmation message must call out hidden entries.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") return Promise.resolve([sampleRecord()]);
			if (type === "get_favorites") return Promise.resolve([]);
			if (type === "get_today_stats")
				return Promise.resolve({
					count: 5,
					chars: 50,
					word_count: 10,
					duration: 20,
				});
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);

		await waitFor(() => {
			expect(screen.getByText("hello world")).toBeTruthy();
		});

		// Activate the favorites filter — visible list becomes empty
		// but the cached stats still report 5 entries.
		const favBtn = screen.getByRole("button", { name: t("history.favorites") });
		fireEvent.click(favBtn);

		await waitFor(() => {
			expect(favBtn.getAttribute("aria-pressed")).toBe("true");
		});

		// Click Clear All — should open the dialog (NOT short-circuit on
		// records.length === 0, because the stats count is 5).
		const clearBtn = screen.getByRole("button", {
			name: t("history.clearAllAria"),
		});
		fireEvent.click(clearBtn);

		//the dialog message is the filter-aware variant that
		// explicitly calls out hidden entries.
		await waitFor(() => {
			expect(
				screen.getByText(t("history.clearAllWithFilterMessage")),
			).toBeTruthy();
		});
		// The default (non-filter) message must NOT be shown.
		expect(screen.queryByText(t("history.clearAllMessage"))).toBeNull();
	});

	it("skips the short-circuit when filter is active but stats count > 0", async () => {
		// Visible records: empty (favorites filter active). Total stats: 3.
		//Without the  fix, handleClearAll would short-circuit on
		// `records.length === 0` and the dialog would never open.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") return Promise.resolve([sampleRecord()]);
			if (type === "get_favorites") return Promise.resolve([]);
			if (type === "get_today_stats")
				return Promise.resolve({
					count: 3,
					chars: 30,
					word_count: 6,
					duration: 12,
				});
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);

		await waitFor(() => {
			expect(screen.getByText("hello world")).toBeTruthy();
		});

		const favBtn = screen.getByRole("button", { name: t("history.favorites") });
		fireEvent.click(favBtn);

		await waitFor(() => {
			expect(favBtn.getAttribute("aria-pressed")).toBe("true");
		});

		// No alertdialog should be present yet.
		expect(screen.queryByRole("alertdialog")).toBeNull();

		const clearBtn = screen.getByRole("button", {
			name: t("history.clearAllAria"),
		});
		fireEvent.click(clearBtn);

		//dialog opens because the stats count is > 0 (even
		// though the visible records list is empty under the filter).
		await waitFor(() => {
			expect(screen.getByRole("alertdialog")).toBeTruthy();
		});
	});

	it("still short-circuits when filter is active AND stats count is 0", async () => {
		// Visible records: empty (favorites filter active). Total stats: 0.
		//the short-circuit SHOULD fire here (no history to clear).
		mockCall.mockImplementation((type: string) => {
			if (type === "get_history") return Promise.resolve([]);
			if (type === "get_favorites") return Promise.resolve([]);
			if (type === "get_today_stats") return Promise.resolve(zeroStats);
			return Promise.resolve({});
		});

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);

		// Wait for the empty state to render.
		await waitFor(() => {
			expect(screen.getByText(t("history.noTranscriptions"))).toBeTruthy();
		});

		// Activate the favorites filter.
		const favBtn = screen.getByRole("button", { name: t("history.favorites") });
		fireEvent.click(favBtn);

		await waitFor(() => {
			expect(favBtn.getAttribute("aria-pressed")).toBe("true");
		});

		// No alertdialog should be present.
		expect(screen.queryByRole("alertdialog")).toBeNull();

		const clearBtn = screen.getByRole("button", {
			name: t("history.clearAllAria"),
		});
		fireEvent.click(clearBtn);

		// Dialog must NOT open — totalCount is 0, nothing to clear.
		// Wait for any pending state updates to settle, then assert
		// no dialog appeared (waitFor polls until the negative
		// assertion holds stably).
		await waitFor(() => {
			expect(screen.queryByRole("alertdialog")).toBeNull();
		});
	});
});

// Regression coverage for the load-error EmptyState variant.
// The History page distinguishes "backend failed to load" from "history is
// genuinely empty": when loadError is set AND records is empty, it renders an
// EmptyState with variant="error" so the failure is visually distinct from a
// genuine empty list (destructive ring + Alert02Icon + role="alert"). This
// matches the Vocabulary/Templates/Microphone load-failure pattern. Removing
// variant="error" would make a backend failure look identical to "no
// transcriptions yet", sending the user down the wrong recovery path.
describe("History load-error EmptyState uses the error variant", () => {
	it('renders role="alert" with destructive styling when the backend load fails', async () => {
		// Every IPC call rejects — useHistoryCache.load() catches,
		// sets loadError, and leaves records as []. The page then
		// renders the load-error EmptyState (variant="error").
		mockCall.mockRejectedValue(new Error("backend unreachable"));

		const { default: HistoryPage } = await import("@/pages/History");
		render(<HistoryPage />);

		await waitFor(() => {
			expect(screen.getByText(t("history.loadFailedTitle"))).toBeTruthy();
		});

		// The error variant of EmptyState wraps the card in a div with
		// role="alert" (the info variant uses role="status"). This is
		// the assertion that fails if variant="error" is removed.
		const alertRegion = screen.getByRole("alert");
		expect(alertRegion).toBeTruthy();
		// The destructive ring + soft wash should be applied.
		expect(alertRegion.className).toContain("destructive");
	});
});
