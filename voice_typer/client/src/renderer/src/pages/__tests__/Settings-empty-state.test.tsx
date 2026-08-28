/**
 *  —  regression test: Settings search empty state.
 *
 * When the user types a search query that matches no row on the active
 * tab, the Settings page must render an empty-state banner with the
 * i18n string "No settings match \"{query}\"" so the user knows the
 * search ran but found nothing (instead of staring at a blank tab).
 *
 * The sentinel `hasAnyVisibleRow` is computed by lifting the per-section
 * visibility calls via the `_filter_settings` function — it bumps a
 * render-phase counter on each positive match, and a layout effect reads
 * the counter to derive the boolean state. This test verifies the
 * end-to-end behaviour (typing a non-matching query shows the banner;
 * clearing the query hides it) without depending on the internal
 * counter mechanism.
 *
 * The query is set via the shared `useGlobalSearch` store (the
 * per-page SearchField was removed — the global title-bar search
 * owns the only search input).
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
	nextThemesMock,
	pythonMock,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall, mockPythonEvent } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * Settings-page render helper. The page's render graph uses Radix
 * `Tooltip` (via SettingRow and other ui primitives); the real App shell
 * wraps the page in a `TooltipProvider` (App.tsx), so tests mounting
 * `<SettingsPage />` directly must provide one too — otherwise every
 * Tooltip render throws "Tooltip must be used within TooltipProvider"
 * and the page mounts empty.
 */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

/** Minimal valid config — same shape as Settings.test.tsx's baseConfig.
 *
 *  Built on the shared `makeConfig` fixture so this file no longer keeps
 *  its own ~120-field copy of `VoiceTyperConfig` (XA-15-2 drift hazard).
 *  Only the fields that differ from `DEFAULT_CONFIG` are overridden here. */
const baseConfig = makeConfig({
	schema_version: 1,
	fast_startup: true,
	llm_preset: "default",
	theme_preset: "custom",
	custom_theme: { light: {}, dark: {} },
});

describe("UX-18: Settings search empty state", () => {
	beforeEach(async () => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
		// Reset the global search store to a fresh instance
		// (vi.resetModules re-evaluates the store module; the dynamic
		// import below resolves the SAME fresh instance SettingsPage
		// will consume — same pattern as Vocabulary tests).
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		useGlobalSearch.setState({ query: "" });
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the 'No settings match' banner when the query matches no row", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		// Wait for the page to load.
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// Set a nonsense query via the global store (the per-page
		// SearchField was removed; the title-bar search owns the input).
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		useGlobalSearch.getState().setQuery("zzzqqqxxxyyy999");

		//the empty-state banner must appear, interpolating the
		// query into the i18n string "No settings match \"{query}\"".
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeTruthy();
		});
	});

	it("hides the 'No settings match' banner when the query is cleared", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		// Type a non-matching query.
		useGlobalSearch.getState().setQuery("zzzqqqxxxyyy999");
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeTruthy();
		});

		// Clear the query — the banner must disappear.
		useGlobalSearch.getState().setQuery("");
		await waitFor(() => {
			expect(
				screen.queryByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeNull();
		});
	});

	it("does NOT render the banner when the query is empty (initial state)", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// No banner on initial render (empty query → no search active).
		expect(screen.queryByText(/No settings match/)).toBeNull();
	});

	it("does NOT render the banner when the query matches a row", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// "appearance" matches the Appearance tab label AND the
		// Appearance section title — so it's a positive match (no banner).
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		useGlobalSearch.getState().setQuery("appearance");

		// After the layout effect settles, the positive match means
		// the empty-state banner must NOT be rendered. waitFor polls
		// until the assertion holds stably (no transient banner).
		await waitFor(() => {
			expect(screen.queryByText(/No settings match/)).toBeNull();
		});
	});
});

describe("Settings initial-load failure shows an error state with Retry", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the error EmptyState instead of an endless 'Loading…' spinner when get_config rejects", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config")
				return Promise.reject(new Error("backend unreachable"));
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		// variant="error" EmptyState replaces the loading branch.
		await waitFor(() => {
			expect(screen.getByText("Couldn't load settings")).toBeTruthy();
		});
		expect(
			screen.getByText(
				"We couldn't load your settings from the backend. Check the logs and try again.",
			),
		).toBeTruthy();
		expect(screen.getByRole("alert")).toBeTruthy();

		const retryButton = screen.getByRole("button", { name: "Retry" });
		expect(retryButton).toBeTruthy();
	});

	it("recovers into the normal page after a successful Retry click", async () => {
		let failGetConfig = true;
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") {
				return failGetConfig
					? Promise.reject(new Error("backend unreachable"))
					: Promise.resolve(baseConfig);
			}
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Couldn't load settings")).toBeTruthy();
		});

		failGetConfig = false;
		fireEvent.click(screen.getByRole("button", { name: "Retry" }));

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});
		// The load-failure card is gone once the config loads. (The
		// recovered page may legitimately contain OTHER role="alert"
		// regions, e.g. the keyboard-permission banner — assert on the
		// error card's own title instead of the alert role.)
		expect(screen.queryByText("Couldn't load settings")).toBeNull();
	});
});
