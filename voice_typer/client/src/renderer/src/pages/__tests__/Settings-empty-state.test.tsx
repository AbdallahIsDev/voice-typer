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
 */
import {
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
const { mockCall, mockPythonEvent } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
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
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
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

		// Wait for the page to load (the tab labels are always visible).
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Type a nonsense query that matches no row label/info/section-title
		// on any tab. Use a long random string to avoid coincidental matches
		// against translated labels.
		const searchInput = document.querySelector(
			'input[type="text"], input:not([type])',
		) as HTMLInputElement | null;
		expect(searchInput).toBeTruthy();
		if (!searchInput) throw new Error("search input not found");

		fireEvent.change(searchInput, { target: { value: "zzzqqqxxxyyy999" } });

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
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		const searchInput = document.querySelector(
			'input[type="text"], input:not([type])',
		) as HTMLInputElement | null;
		expect(searchInput).toBeTruthy();
		if (!searchInput) throw new Error("search input not found");

		// Type a non-matching query.
		fireEvent.change(searchInput, { target: { value: "zzzqqqxxxyyy999" } });
		await waitFor(() => {
			expect(
				screen.getByText('No settings match "zzzqqqxxxyyy999"'),
			).toBeTruthy();
		});

		// Clear the query — the banner must disappear.
		fireEvent.change(searchInput, { target: { value: "" } });
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
			expect(screen.getByText("Appearance")).toBeTruthy();
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
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		const searchInput = document.querySelector(
			'input[type="text"], input:not([type])',
		) as HTMLInputElement | null;
		expect(searchInput).toBeTruthy();
		if (!searchInput) throw new Error("search input not found");

		// "appearance" matches the Appearance tab hint AND the Appearance
		// section title — so it's a positive match (no banner).
		fireEvent.change(searchInput, { target: { value: "appearance" } });

		// After the layout effect settles, the positive match means
		// the empty-state banner must NOT be rendered. waitFor polls
		// until the assertion holds stably (no transient banner).
		await waitFor(() => {
			expect(screen.queryByText(/No settings match/)).toBeNull();
		});
	});
});
