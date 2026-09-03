/**
 * + tests for the Settings page.
 *
 * — cross-tab search grouping: when the global query matches
 * Settings rows on OTHER sub-pages, a "Results from other tabs"
 * section lists them grouped by tab; each match is a button that
 * navigates to its tab with a rowHint deep-link. When the query
 * matches nothing anywhere, the existing "No settings match" banner
 * still renders (and the cross-tab section does not).
 *
 * — save-error surface: useSettingsConfig's per-flush `error`
 * string renders as a visible destructive banner (aria-live="polite")
 * under the page heading until the next successful save clears it.
 */
import {
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	navigationMock,
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
// The REAL useNavigation auto-navigates to the best-matching sub-page on
// query change (the proven auto-switch), which would yank the page off
// the General sub-page mid-test. Mock it so navigation is assertable and
// the page stays put.
vi.mock("@/hooks/useNavigation", () => navigationMock());

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

const baseConfig = makeConfig({
	schema_version: 1,
	fast_startup: true,
	llm_preset: "default",
	theme_preset: "custom",
	custom_theme: { light: {}, dark: {} },
});

function happyCallMock() {
	mockCall.mockImplementation((type: string) => {
		if (type === "get_config") return Promise.resolve(baseConfig);
		if (type === "set_config") return Promise.resolve({ success: true });
		return Promise.resolve({});
	});
}

describe("Settings — cross-tab search results", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders grouped results from other tabs and navigates on click", async () => {
		happyCallMock();
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		useGlobalSearch.setState({ query: "" });

		const { default: SettingsPage } = await import("@/pages/Settings");
		// The cross-section results card renders on SECTION pages only
		// (the hub lists matched labels inline inside its rows). Mount
		// on General so the query's matches on OTHER section pages land
		// in the card.
		renderWithProviders(<SettingsPage page="settingsGeneral" />);
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// "Appearance" exists only on the appearance section page; the
		// query is typed while the General section page is active.
		useGlobalSearch.getState().setQuery("Appearance");

		const section = await waitFor(() =>
			screen.getByTestId("settings-other-tabs-results"),
		);
		expect(section).toBeTruthy();

		// The group heading names the other section page (the
		// SECTION_TITLE_BY_PAGE title for the Appearance section).
		// Multiple nodes may carry the text (heading + row buttons).
		expect(screen.getAllByText("Appearance").length).toBeGreaterThan(0);

		// Clicking a match deep-links to that section page with a rowHint.
		fireEvent.click(
			screen.getAllByRole("button", { name: "Appearance" })[0] as HTMLElement,
		);
		await waitFor(() => {
			expect(stableMocks.mockNavigate).toHaveBeenCalledWith(
				"settingsAppearance",
				expect.objectContaining({
					settingsScrollTarget: expect.objectContaining({
						rowHint: expect.stringContaining("Appearance"),
					}),
				}),
			);
		});
	});

	it("does NOT list the active tab's own rows in the cross-tab section", async () => {
		happyCallMock();
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		useGlobalSearch.setState({ query: "" });

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// "Launch at login" is a General-tab row; on the General tab the
		// cross-tab section must NOT appear for it (it's filtered inline).
		useGlobalSearch.getState().setQuery("Launch at login");
		await waitFor(() => {
			expect(screen.queryByTestId("settings-other-tabs-results")).toBeNull();
		});
	});

	it("keeps the 'No settings match' banner when nothing matches anywhere", async () => {
		happyCallMock();
		const { useGlobalSearch } = await import("@/hooks/useGlobalSearch");
		useGlobalSearch.setState({ query: "" });

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		useGlobalSearch.getState().setQuery("zzzqqqxxxyyy999");
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeTruthy();
		});
		expect(screen.queryByTestId("settings-other-tabs-results")).toBeNull();
	});
});

describe("Settings — save-error banner", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders a destructive aria-live banner carrying the hook's error text", async () => {
		// set_config rejects → useSettingsConfig sets `error`.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config")
				return Promise.reject(
					new Error("field 'history_max_entries' must be in [10, 1000000]"),
				);
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		// Switches live on the section pages now — the hub's rows are
		// plain buttons. Mount on Privacy, whose first switch is the
		// Crash Recovery toggle (PrivacySettingsSection).
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// Flip the Crash Recovery switch to trigger a save.
		const sw = screen.getByRole("switch", { name: "Crash Recovery" });
		fireEvent.click(sw);

		const banner = await waitFor(() =>
			screen.getByTestId("settings-save-error"),
		);
		expect(banner.getAttribute("aria-live")).toBe("polite");
		expect(banner.getAttribute("role")).toBe("status");
		expect(banner.textContent).toContain("history_max_entries");
	});

	it("renders no banner while saves succeed", async () => {
		happyCallMock();
		const { default: SettingsPage } = await import("@/pages/Settings");
		// Switches live on the section pages now — mount on Privacy.
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});
		const sw = screen.getByRole("switch", { name: "Crash Recovery" });
		fireEvent.click(sw);
		// Give the flush microtask a beat.
		await waitFor(() => {
			expect(stableMocks.mockCall).toHaveBeenCalledWith(
				"set_config",
				expect.anything(),
			);
		});
		expect(screen.queryByTestId("settings-save-error")).toBeNull();
	});
});
