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
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

		// StatCards renders three labelled cards.
		expect(screen.getByText("Voice Dictations")).toBeTruthy();
		expect(screen.getByText("Text Transcribed")).toBeTruthy();
		expect(screen.getByText("Dictation Time")).toBeTruthy();
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
});
