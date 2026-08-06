/**
 * Tests for the Settings page — batched config writes (PERF-002).
 *
 * The Settings page owns the `updateConfig` / `updateConfigDebounced`
 * callbacks which persist config changes to the Python backend via the
 * `set_config` IPC.  PERF-002 batches writes so multiple rapid changes
 * within a debounce window collapse into a single `set_config` call,
 * avoiding redundant IPC traffic (the backend's `set_config` accepts a
 * partial dict — see IPC_CONFIG_ALLOWLIST — so a single call can carry
 * any number of changed keys).
 *
 * We mock the Python bridge, the hugeicons renderer, sonner, and
 * next-themes.  The Radix-UI-based Select/Switch/Tooltip components are
 * left un-mocked because jsdom supports them well enough for the color
 * picker interactions exercised here.
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

// Stub every icon used by Settings + its transitive children (SearchField,
// HotkeyPicker, ui/select, PrivacySettingsSection, etc.) with `{ name }`
// tagged objects so the HugeiconsIcon mock can surface which icon was
// rendered via data-name.  Vitest's vi.mock requires named exports to be
// declared explicitly, so we enumerate the full set consumed by the
// Settings render graph.  (sonner / next-themes are mocked separately, so
// ui/sonner.tsx's icons aren't needed here.)
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		AlertCircleIcon: make("AlertCircleIcon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Delete01Icon: make("Delete01Icon"),
		//TroubleshootingSettingsSection renders the destructive
		// "Reset to Defaults" button with `Delete02Icon` (a distinct trash
		// glyph) instead of the previous RefreshIcon that was visually
		// indistinguishable from "Re-run Wizard"'s ArrowTurnBackwardIcon.
		//The mock MUST export Delete02Icon or the  regression
		// test (and the existing Re-run wizard test) crash with
		// "No 'Delete02Icon' export is defined on the mock".
		Delete02Icon: make("Delete02Icon"),
		File02Icon: make("File02Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		// KeyboardPermissionBanner (now mounted on Settings via
		// the page-level import) renders AlertCircleIcon + Settings03Icon
		// for the amber "click to fix" banner. Adding both to the mock
		// keeps the existing render-graph coverage complete.
		Settings03Icon: make("Settings03Icon"),
		Sun01Icon: make("Sun01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
});

// sonner is imported transitively via useSnackbar → toast.  Stub it so
// the test doesn't depend on sonner's portal/DOM rendering.
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

// next-themes is imported by components/ui/sonner.tsx (which is pulled
// in via the Settings page's transitive import graph through useSnackbar
// → sonner).  Stub it so the test doesn't depend on next-themes' context
// provider.
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { VoiceTyperConfig } from "@/types/config";

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

/** A complete, valid VoiceTyperConfig with `theme_preset: "custom"` so the
 *  color picker renders on first paint.  Only the theme-related fields are
 *  meaningful for these tests; the rest are populated with sensible
 *  defaults so the various Settings sections don't blow up on render.
 *
 *  Built on the shared `makeConfig` fixture so this file no longer keeps
 *  its own ~140-field copy of `VoiceTyperConfig` (the historical drift
 *  hazard called out in XA-15-2). Only the fields that actually matter
 *  for the color-picker / theme-preset tests are overridden here. */
const baseConfig: VoiceTyperConfig = makeConfig({
	schema_version: 1,
	fast_startup: true,
	llm_preset: "default",
	theme_preset: "custom",
	custom_theme: {
		light: {
			"--bg": "#ffffff",
			"--bg-subtle": "#f5f5f5",
			"--text": "#000000",
			"--text-muted": "#666666",
			"--accent": "#3b82f6",
			"--border": "#e5e7eb",
		},
		dark: {
			"--bg": "#000000",
			"--bg-subtle": "#111111",
			"--text": "#ffffff",
			"--text-muted": "#999999",
			"--accent": "#60a5fa",
			"--border": "#222222",
		},
	},
});

/** Count `set_config` IPC calls captured by mockCall. */
function setConfigCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	).length;
}

/** Return the payload of the most recent `set_config` call (or null). */
function lastSetConfigPayload(): Record<string, unknown> | null {
	const setConfigCalls = mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "set_config",
	) as Array<[string, Record<string, unknown>?]>;
	if (setConfigCalls.length === 0) return null;
	return setConfigCalls[setConfigCalls.length - 1]?.[1] ?? null;
}

describe("Settings page — PERF-002 batched config writes", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Reset the module registry so Settings' module-level cache
		// (_cachedConfig) is re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("mounts and loads config via get_config without firing set_config", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		// The Appearance section heading renders once config loads.
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Loading the config must NOT trigger a save — the
		// lastSavedConfigRef baseline is seeded in loadConfig so the
		// initial snapshot isn't re-persisted as a "change".
		expect(setConfigCallCount()).toBe(0);
	});

	it("batches 3 rapid color-picker changes into a single set_config call", async () => {
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

		// The color pickers live in the ThemeSettingsSection, which is
		// only rendered when the Appearance tab is active.  Click the
		// tab label to navigate there.
		fireEvent.click(screen.getByText("Appearance"));

		// Wait for ThemeSettingsSection to mount and render the color
		// pickers (it calls setCustomDraft during render which needs an
		// extra React pass to finalise).
		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(3);
		});
		const colorInputs = document.querySelectorAll('input[type="color"]');
		const [input0, input1, input2] = colorInputs;
		if (!input0 || !input1 || !input2) {
			throw new Error("expected at least 3 color inputs");
		}

		// Change 3 colors in rapid succession — each change schedules a
		// 300ms per-key debounce via updateConfigDebounced("custom_theme", …).
		// All three use the same key ("custom_theme") so the per-key
		// debounce cancels and reschedules a single timer; when that
		// timer fires, updateConfig merges the final value into the
		// pending buffer and schedules a microtask flush.  The flush
		// sends ONE set_config call with { custom_theme: <latest> }.
		fireEvent.input(input0, { target: { value: "#ff0000" } });
		fireEvent.input(input1, { target: { value: "#00ff00" } });
		fireEvent.input(input2, { target: { value: "#0000ff" } });

		// Wait for the 300ms per-key debounce + microtask flush +
		// set_config IPC to complete.  waitFor polls (flushing
		// microtasks between polls) until the assertion passes or the
		// timeout (default 1000ms) elapses.
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		// The single set_config payload must carry the custom_theme
		// key (the diff against lastSavedConfigRef).
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("custom_theme");
	});

	it("re-saves when a setting is reverted (diff is against the last saved value, not the original load)", async () => {
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

		// Navigate to the Appearance tab so the color pickers are visible.
		// The ThemeSettingsSection calls setCustomDraft during render, so we
		// wait for the color inputs to actually appear before proceeding.
		fireEvent.click(screen.getByText("Appearance"));
		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		const colorInputs = document.querySelectorAll('input[type="color"]');
		// Narrow once: the waitFor above proves >=1 color input exists, so
		// `firstColorInput` is non-null. Using it throughout also lets us
		// satisfy `noUncheckedIndexedAccess` without non-null assertions
		// (which biome's `noNonNullAssertion` rule forbids).
		const firstColorInput = colorInputs[0] as HTMLInputElement;
		expect(firstColorInput).toBeDefined();

		// Capture the original hex value of the first color input, then
		// change it to something else, then change it back. The
		// diff is computed against `lastSavedConfigRef` (the last value
		// the backend confirmed), NOT the original config loaded at mount.
		// So after the first save updates the baseline to #abcdef,
		// reverting to the original hex is still a real diff and triggers
		// a second set_config call.  This documents that behaviour so
		// future refactors don't accidentally start diffing against the
		// initial load (which would silently drop reverts).
		const originalValue = firstColorInput.value;

		// Change to a different color and wait for the debounced save.
		fireEvent.input(firstColorInput, { target: { value: "#abcdef" } });
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});

		// Change back to the original color — the baseline now has
		// #abcdef, so this is a non-empty diff and a second set_config
		// fires carrying the reverted custom_theme.
		fireEvent.input(firstColorInput, { target: { value: originalValue } });
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(2);
		});

		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("custom_theme");
	});

	// D1-FIX (b-review Finding 1): the "Re-run setup wizard" button in the
	// Troubleshooting section calls `updateConfig({ onboarding_completed:
	// false })` then `onNavigate("onboarding")`.  Previously
	// `updateConfig` only updated Settings.tsx's LOCAL `config` state and
	// queued a backend `set_config` IPC — it did NOT touch the Zustand
	// `appStore.config` snapshot that App.tsx's route guard reads.  The
	// appStore only learned about the change later (via the
	// `config_changed` push event), so the route guard fired on the very
	// next render, saw the stale `true` value, and bounced the user back
	// to home — the onboarding wizard was never shown.
	//
	// The fix mirrors `mergeConfig(updates)` into the appStore
	// synchronously inside `updateConfig`.  These tests verify that sync:
	//   1. The appStore's `onboarding_completed` becomes `false`
	//      SYNCHRONOUSLY (before any microtask flush), so the route guard
	//      in App.tsx sees the new value on the next render.
	//   2. The `onNavigate` callback fires with `"onboarding"` (the
	//      wizard button's navigation call).
	//   3. The backend `set_config` IPC is still queued (the sync mirror
	//      does NOT replace the persisted write — it only updates the
	//      in-memory snapshot).
	it("Re-run setup wizard synchronously mirrors onboarding_completed=false into the appStore", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		// vi.resetModules() in beforeEach clears the module registry,
		// so we must dynamically import useAppStore AFTER the reset to
		// get the SAME fresh instance the dynamically-imported
		// SettingsPage will use.  Importing it at the top of the file
		// would pin us to the pre-reset instance and the assertion
		// below would silently read the wrong store.
		const { useAppStore } = await import("@/stores/appStore");
		// Seed the appStore with the completed-onboarding config so
		// we can observe the transition to `false` after the click.
		// (In production this is populated by useTheme's get_config
		// call on connect; here we seed it directly.)
		useAppStore.getState().setConfig(baseConfig);
		expect(useAppStore.getState().config?.onboarding_completed).toBe(true);

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage />);

		// Wait for the page to load (the tab labels are always visible).
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// The wizard button lives in the Privacy tab's Troubleshooting
		// section.  Click the Privacy tab to mount it.
		fireEvent.click(screen.getByText("Privacy"));

		// Wait for the wizard button to mount (it's filtered by the
		// search-visible check, but the default empty filter shows it).
		const wizardButton = await waitFor(() =>
			screen.getByRole("button", { name: "Re-run setup wizard" }),
		);

		// Clear the mock call history so we can assert the post-click
		// set_config IPC precisely.
		mockCall.mockClear();
		fireEvent.click(wizardButton);

		// The click handler is async (awaits updateConfig which awaits
		// the microtask flush).  Wait for navigate to be called —
		// that's the LAST statement in the click handler, so by the
		// time it fires the mergeConfig call has already executed.
		await waitFor(() => {
			expect(mockNavigate).toHaveBeenCalledWith("onboarding");
		});

		// D1-FIX assertion: the appStore snapshot must reflect
		// onboarding_completed=false SYNCHRONOUSLY (i.e. by the time
		// onNavigate fired).  Before the fix this assertion failed
		// because mergeConfig was never called from updateConfig —
		// the appStore still held `true` and App.tsx's route guard
		// would bounce the user back to home on the next render.
		expect(useAppStore.getState().config?.onboarding_completed).toBe(false);

		// The sync mirror must NOT replace the backend write — the
		// `set_config` IPC is still queued (via the microtask flush)
		// so the change is persisted to disk for the next launch.
		await waitFor(() => {
			expect(setConfigCallCount()).toBe(1);
		});
		const payload = lastSetConfigPayload();
		expect(payload).not.toBeNull();
		expect(payload).toHaveProperty("onboarding_completed", false);
	});

	//regression test: the destructive "Reset to Defaults" button
	// MUST render with a distinct trash/delete icon (Delete02Icon), NOT the
	// same icon as the non-destructive "Re-run setup wizard" button
	// (ArrowTurnBackwardIcon). The original bug was that both buttons shared
	// a RefreshIcon — visually identical despite semantically opposite
	// actions — so users could not tell at a glance which button was
	// destructive.
	//
	// The fix lives in TroubleshootingSettingsSection.tsx (extracted from
	//the old 1125-line Settings.tsx monolith — see ). It is verified
	// here end-to-end via the Settings page render graph (the Privacy tab
	// mounts TroubleshootingSettingsSection) so a future refactor that
	// accidentally re-unifies the icons would fail this test.
	//
	// The HugeiconsIcon mock (top of file) renders each icon as
	// `<span data-testid="hugeicon" data-name={icon?.name}>`, so we assert
	// on `data-name` to pin the exact icon glyph.
	it("S5-CR-103: Reset to Defaults uses Delete02Icon, distinct from Re-run Wizard's ArrowTurnBackwardIcon", async () => {
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

		// The Troubleshooting section lives on the Privacy tab.
		fireEvent.click(screen.getByText("Privacy"));

		// Wait for both buttons to mount.
		const resetButton = await waitFor(() =>
			screen.getByRole("button", { name: "Reset to Defaults" }),
		);
		const wizardButton = await waitFor(() =>
			screen.getByRole("button", { name: "Re-run setup wizard" }),
		);

		// Each button renders exactly one HugeiconsIcon span (the mock
		// surfaces the icon name via `data-name`). Query within each
		// button's subtree so multiple icons in the section don't
		// contaminate the assertion.
		const resetIcon = resetButton.querySelector(
			'[data-testid="hugeicon"]',
		) as HTMLElement | null;
		const wizardIcon = wizardButton.querySelector(
			'[data-testid="hugeicon"]',
		) as HTMLElement | null;

		expect(wizardIcon).toBeTruthy();

		//Reset to Defaults MUST use the trash glyph.
		expect(resetIcon?.getAttribute("data-name")).toBe("Delete02Icon");
		// Re-run Wizard MUST use the back-arrow glyph (NOT Delete02Icon).
		expect(wizardIcon?.getAttribute("data-name")).toBe("ArrowTurnBackwardIcon");
		// Belt-and-braces: the two icons must not be the same glyph.
		expect(resetIcon?.getAttribute("data-name")).not.toBe(
			wizardIcon?.getAttribute("data-name"),
		);
	});
});
