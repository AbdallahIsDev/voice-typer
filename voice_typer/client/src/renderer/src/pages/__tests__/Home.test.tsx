/**
 * Tests for the Home page.
 *
 * Home is the landing page of the app: a record/stop toggle button, a
 * status pill, the hotkey chip, today's stats (StatCards), and a recent
 * activity list (ActivityList). Data is fetched on mount via usePython
 * and cached in module-level + localStorage so re-visits are instant.
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

// Hoist the mock call/event handlers so they're available inside the
// vi.mock factory (which is hoisted to the top of the file by vitest
// and runs before any other code).
const { mockCall, mockPythonEvent, mockNavigate } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	mockNavigate: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: mockNavigate }),
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

// Stub every icon used by Home + its children (StatCards, ActivityList)
// with `{ name }` tagged objects so the HugeiconsIcon mock can surface
// which icon was rendered.
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		ClipboardPasteIcon: make("ClipboardPasteIcon"),
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Mic02Icon: make("Mic02Icon"),
		// R8: LastUpdatedIndicator (rendered inside Home.tsx) imports RefreshIcon
		// — must be in the mock list or `import { RefreshIcon }` returns undefined
		// and HugeiconsIcon crashes with "icon is undefined".
		RefreshIcon: make("RefreshIcon"),
		Share08Icon: make("Share08Icon"),
		StarIcon: make("StarIcon"),
		StopIcon: make("StopIcon"),
		TextIcon: make("TextIcon"),
		Tick02Icon: make("Tick02Icon"),
		Time02Icon: make("Time02Icon"),
		//test: LastTranscriptionPreview renders Undo + Re-paste
		// buttons when lastText is set; the icons must be in the mock
		// or HugeiconsIcon throws "No 'Undo02Icon' export is defined".
		Undo02Icon: make("Undo02Icon"),
	};
});

describe("Home page", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
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
		render(<Home />);

		// The status pill renders "READY" for the idle state.
		expect(screen.getByText("READY")).toBeTruthy();
		// The hotkey chip renders the default "F2" value.
		expect(screen.getByText("F2")).toBeTruthy();
	});

	it("shows a spinner while initial data is loading", async () => {
		// A never-resolving promise keeps `initialLoading` true forever,
		// so the spinner sections stay mounted.
		mockCall.mockImplementation(() => new Promise(() => {}));
		const { default: Home } = await import("@/pages/Home");
		render(<Home />);

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
		render(<Home />);

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
		render(<Home />);

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
		render(<Home />);

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
		render(<Home />);

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
		render(<Home />);

		// Wait for cfg to load (the button's disabled state depends on it).
		await waitFor(() => {
			const shareButton = screen.getByRole("button", { name: "Share Stats" });
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
		render(<Home />);

		await waitFor(() => {
			const shareButton = screen.getByRole("button", { name: "Share Stats" });
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
		render(<Home />);

		await waitFor(() => {
			const shareButton = screen.getByRole("button", { name: "Share Stats" });
			expect(shareButton).not.toBeDisabled();
		});
	});
});
