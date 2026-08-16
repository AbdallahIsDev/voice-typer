/**
 * Tests for the Home page.
 *
 * Home is the landing page of the app: a record/stop toggle button, a
 * single dynamic status line under it (default hotkey hint / preparing /
 * red error text), today's stats (StatCards), and a recent activity list
 * (ActivityList). Data is fetched on mount via usePython and cached in
 * module-level + localStorage so re-visits are instant.
 *
 * We mock the Python bridge and re-import Home freshly in each test
 * (via vi.resetModules + dynamic import) so the module-level caches
 * (_cachedStats, _cachedRecent) don't leak between tests.
 */
import {
	act,
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
	navigationMock,
	pythonMock,
	resetStableMocks,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";
import { TooltipProvider } from "@/components/ui/tooltip";

const { mockCall, mockPythonEvent, mockNavigate } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

describe("Home page", () => {
	beforeEach(() => {
		// Reset the shared singletons (mockCall, mockPythonEvent, …).
		resetStableMocks();
		localStorage.clear();
		// Reset the module registry so Home's module-level caches
		// (_cachedStats, _cachedRecent) are re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("mounts without crashing", async () => {
		// Keep the backend calls pending so no async state updates fire
		// during the test (avoids spurious act() warnings). We only need
		// to verify the initial render is non-empty.
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		// The dynamic status line shows the default hotkey hint for the
		// idle state (config still loading, no error, no preparing state).
		expect(screen.getByText("or click to dictate")).toBeTruthy();
		// The hotkey chip renders the default "F2" value.
		expect(screen.getByText("F2")).toBeTruthy();
	});

	it("shows a spinner while initial data is loading", async () => {
		// A never-resolving promise keeps `initialLoading` true forever,
		// so the spinner sections stay mounted.
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		// The stats spinner section exposes an aria-label.
		expect(screen.getByLabelText("Loading today's stats")).toBeTruthy();
		// The recent-activity spinner section also exposes an aria-label.
		expect(screen.getByLabelText("Loading recent activity")).toBeTruthy();
	});

	it("renders StatCards when today's stats are provided via cache", async () => {
		// Pre-populate the localStorage cache so Home initialises `stats`
		// from cache on the very first render (no async wait needed).
		const stats = { count: 5, chars: 250, word_count: 50, duration: 120 };
		localStorage.setItem("vt_home_stats_cache", JSON.stringify(stats));
		// Keep the backend call pending so the effect doesn't overwrite
		// the cached stats before we assert.
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		//StatCards renders three labelled cards. : card labels
		// are i18n-driven (dashboard.cards.dictations/chars/duration).
		expect(screen.getByText("Dictations")).toBeTruthy();
		expect(screen.getByText("Characters")).toBeTruthy();
		expect(screen.getByText("Duration")).toBeTruthy();
		// The "Today's Stats" heading is shown above the cards.
		expect(screen.getByText(/Today's Stats/i)).toBeTruthy();
		// No spinner should be visible because cached stats exist.
		expect(screen.queryByLabelText("Loading today's stats")).toBeNull();
	});

	it("navigates to 'history' when the View all button is clicked", async () => {
		// Pre-populate the recent-records cache so ActivityList renders
		// with a "View all" button.
		const recent = [
			{
				id: 1,
				text: "hello world",
				timestamp: new Date().toISOString(),
				duration: 1,
				model: "parakeet",
				device: "cpu",
				word_count: 2,
				char_count: 11,
				favorite: 0,
				language: "en",
			},
		];
		localStorage.setItem("vt_home_recent_cache", JSON.stringify(recent));
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		// The "View all" button is rendered by ActivityList.
		const viewAllButton = screen.getByText("View all");
		fireEvent.click(viewAllButton);
		expect(mockNavigate).toHaveBeenCalledTimes(1);
		expect(mockNavigate).toHaveBeenCalledWith("history");
	});

	// ── BG-6 (PVT-047): transcription result must be in an aria-live region ──

	it("BG-6: renders transcription text inside an aria-live='polite' region", async () => {
		// Pre-populate the stats cache so the page renders without the
		// stats spinner; we don't need real stats for this a11y test.
		const stats = { count: 0, chars: 0, word_count: 0, duration: 0 };
		localStorage.setItem("vt_home_stats_cache", JSON.stringify(stats));
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		// The transcription_final handler was captured by mockPythonEvent
		// when Home mounted. Find it and invoke it with a fake result.
		const transcriptionFinalCall = mockPythonEvent.mock.calls.find(
			(c) => c[0] === "transcription_final",
		);
		expect(transcriptionFinalCall).toBeDefined();
		// `expect(...).toBeDefined()` doesn't narrow the TS type, so use
		// a non-null assertion. The preceding expect guards the runtime
		// case; this assertion only silences the static-type check.
		const handler = transcriptionFinalCall?.[1];

		await act(async () => {
			handler({ text: "hello world" });
		});

		// The transcribed text must be rendered inside an ancestor that
		// carries aria-live="polite" so screen readers announce it.
		const textEl = screen.getByText("hello world");
		const liveRegion = textEl.closest('[aria-live="polite"]');
		expect(liveRegion).not.toBeNull();
		expect(liveRegion?.getAttribute("aria-live")).toBe("polite");
	});

	// ── BG-7: ActivityList empty-state must render when recent is empty AND !initialLoading ──

	it("BG-7: renders ActivityList empty-state (not a blank gap) when recent is empty and not loading", async () => {
		// Pre-populate the stats cache so `initialLoading` initialises
		// to false (because loadCachedStats() !== null), but leave the
		// recent-activity cache empty. Before the BG-7 fix this produced
		// a blank gap below the stats because Home short-circuited on
		// `recent.length > 0` and the spinner branch was gated on
		// `initialLoading` (false here). The fix renders ActivityList
		// unconditionally, letting its empty-state branch surface the
		// "No recent activity" message + "View all" navigation link.
		const stats = { count: 0, chars: 0, word_count: 0, duration: 0 };
		localStorage.setItem("vt_home_stats_cache", JSON.stringify(stats));
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		// ActivityList's empty-state branch renders both the
		// "No recent activity" hint AND the "View all" link.
		expect(screen.getByText("No recent activity")).toBeTruthy();
		expect(screen.getByText("View all")).toBeTruthy();
		// No spinner should be visible because we're not in the
		// initialLoading && recent.length === 0 case.
		expect(screen.queryByLabelText("Loading recent activity")).toBeNull();
	});

	// ── BG-10 (partial): Share Stats button uses canShareStats helper ──

	// Minimal config mock — Home.tsx only reads `hotkey` and `asr_backend`
	// from the cfg object, so we provide just those fields. The mock is
	// cast to `any` at the call site (call<VoiceTyperConfig>) so the
	// partial shape is fine for the test.
	const MOCK_CFG = { hotkey: "<f2>", asr_backend: "parakeet" };

	it("BG-10: Share Stats button is disabled when todayCount=0 and no recent history", async () => {
		// Stats cache: today's count is 0.
		const stats = { count: 0, chars: 0, word_count: 0, duration: 0 };
		localStorage.setItem("vt_home_stats_cache", JSON.stringify(stats));
		// Recent cache is empty (no past transcriptions).
		// mockCall: resolve get_config so cfg loads; never resolve
		// get_today_stats / get_history so the cached values persist.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CFG);
			return new Promise(() => {});
		});
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		// Wait for cfg to load (the button's disabled state depends on it).
		await waitFor(() => {
			const shareButton = screen.getByRole("button", { name: "Share stats" });
			// todayCount=0 AND recent.length=0 → canShareStats returns
			// false → button must be disabled.
			expect(shareButton).toBeDisabled();
		});
	});

	it("BG-10: Share Stats button is ENABLED when todayCount=0 but recent history exists", async () => {
		// Stats cache: today's count is 0 (user hasn't dictated today).
		const stats = { count: 0, chars: 0, word_count: 0, duration: 0 };
		localStorage.setItem("vt_home_stats_cache", JSON.stringify(stats));
		// Recent cache has one record → recent.length > 0 → the BG-10
		// fix treats this as "has past transcriptions" and enables the
		// button via canShareStats({ todayCount: 0, totalCount: 1 }).
		const recent = [
			{
				id: 1,
				text: "past dictation",
				timestamp: new Date().toISOString(),
				duration: 1,
				model: "parakeet",
				device: "cpu",
				word_count: 2,
				char_count: 14,
				favorite: 0,
				language: "en",
			},
		];
		localStorage.setItem("vt_home_recent_cache", JSON.stringify(recent));
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CFG);
			return new Promise(() => {});
		});
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		await waitFor(() => {
			const shareButton = screen.getByRole("button", { name: "Share stats" });
			// todayCount=0 BUT recent.length > 0 → canShareStats
			// returns true → button must NOT be disabled. This is
			// the BG-10 regression fix: previously the button was
			// gated on `stats.count === 0` alone, hiding it on the
			// first run of the day even when past history existed.
			expect(shareButton).not.toBeDisabled();
		});
	});

	it("BG-10: Share Stats button is ENABLED when todayCount > 0 (regardless of recent)", async () => {
		// Stats cache: today's count is 5 (user has dictated today).
		const stats = { count: 5, chars: 250, word_count: 50, duration: 120 };
		localStorage.setItem("vt_home_stats_cache", JSON.stringify(stats));
		// Recent cache is empty — but todayCount > 0 is sufficient for
		// canShareStats to return true.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(MOCK_CFG);
			return new Promise(() => {});
		});
		const { default: Home } = await import("@/pages/Home");
		render(<TooltipProvider>{<Home />}</TooltipProvider>);

		await waitFor(() => {
			const shareButton = screen.getByRole("button", { name: "Share stats" });
			expect(shareButton).not.toBeDisabled();
		});
	});
});
