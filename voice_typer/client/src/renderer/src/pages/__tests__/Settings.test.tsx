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

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + the standard vi.mock registrations, one line
// per module. The destructure below mirrors the names the old
// vi.hoisted block exported, so the test bodies are untouched.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	navigationMock,
	nextThemesMock,
	pythonMock,
	resetStableMocks,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const {
	mockCall,
	mockNavigate,
	mockPendingConsentField,
	mockConsumeConsentField,
} = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => navigationMock());
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());
vi.mock("sonner", () => sonnerMock());
vi.mock("next-themes", () => nextThemesMock());

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
		// Reset the shared singletons (mockCall, the consent channel, …)
		// and restore the consent-field defaults.
		resetStableMocks();
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
		renderWithProviders(<SettingsPage page="settingsGeneral" />);

		// The page heading renders once config loads. The 4-tab
		// SegmentedControl at the top of Settings has been removed
		// (ADR-0021 — the tabs now live in the sidebar as a nested
		// Settings submenu), so the marker that the page has mounted
		// is now the PageHeading title ("Settings") rather than the
		// old SegmentedControl's "Appearance" option label.
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
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
		// Mount directly on the Appearance sub-page — the
		// SegmentedControl tab UI has been removed (ADR-0021), so
		// the test can't click the "Appearance" tab label anymore.
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		// Wait for the page heading to render (the page is now mounted
		// directly on Appearance — no tab click needed).
		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

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
		// Mount directly on the Appearance sub-page (ADR-0021 — the
		// SegmentedControl tab UI has been removed; tests can't click
		// the "Appearance" tab label anymore).
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(screen.getByText("Settings")).toBeTruthy();
		});

		// ThemeSettingsSection is rendered directly on the Appearance
		// sub-page (no tab click needed). It calls setCustomDraft during
		// render, so wait for the color inputs to actually appear before
		// proceeding.
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
		// The wizard button lives in the Privacy sub-page's
		// Troubleshooting section. Mount directly on Privacy
		// (ADR-0021 — the SegmentedControl tab UI has been removed).
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);

		// Wait for the Privacy sub-page to load. The Privacy
		// section heading rendered by PrivacySettingsSection is
		// "Privacy & Consent" (i18n key settings.privacy.privacyTitle);
		// match on a substring regex so the test still passes if the
		// exact wording changes (the test only needs to know the
		// Privacy sub-page has mounted, not assert the heading text).
		await waitFor(() => {
			expect(screen.getByText(/Privacy/)).toBeTruthy();
		});

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

	it("consumes a consent deep-link: jumps to the Privacy tab and highlights the exact toggle row", async () => {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		// A consent refusal elsewhere (mic test / level monitor /
		// dictation gate) navigated here with
		// ``{ consentField: "voice_biometric_consent" }``.
		//
		// After ADR-0021, the consent deep-link lands on the
		// Privacy sub-page (the nav store sends `settingsPrivacy`
		// when a consent_field is present — see App.tsx navigate
		// event handler). The test must mount the page with
		// `page="settingsPrivacy"` to mirror the runtime routing;
		// otherwise the consent rows (rendered by
		// PrivacySettingsSection) are not in the DOM.
		mockPendingConsentField.mockReturnValue("voice_biometric_consent");
		mockConsumeConsentField.mockReturnValue("voice_biometric_consent");

		const { default: SettingsPage } = await import("@/pages/Settings");
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);

		// The deep-link forces the Privacy tab and the Voice Biometric
		// row renders with the ``data-consent-field`` scroll target +
		// temporary highlight ring.
		await waitFor(() => {
			const row = document.querySelector(
				'[data-consent-field="voice_biometric_consent"]',
			);
			expect(row).toBeTruthy();
			expect(row?.className).toContain("ring-");
		});

		// The pending target was consumed exactly once (one-shot).
		expect(mockConsumeConsentField).toHaveBeenCalledTimes(1);
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
		// The Troubleshooting section lives on the Privacy sub-page.
		// Mount directly on Privacy (ADR-0021 — SegmentedControl tab
		// UI has been removed).
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);

		await waitFor(() => {
			expect(screen.getByText(/Privacy/)).toBeTruthy();
		});

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

	// ── Finding #127 part (b): "Reset Accessibility Permission" ────────
	// The button lives in TroubleshootingSettingsSection (Privacy tab)
	// and is macOS-only (UA-gated like KeyboardPermissionBanner). Clicking
	// it invokes the `reset_macos_accessibility` IPC; the backend runs
	// `tccutil reset Accessibility <bundle-id>` (runtime-resolved) and
	// re-opens System Settings.
	const macUA =
		"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
		"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
	const linuxUA =
		"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " +
		"(KHTML, like Gecko) Chrome/126.0 Safari/537.36";
	const windowsUA =
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +
		"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";

	async function renderSettingsOnPrivacyTabForResetA11y() {
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		// After ADR-0021, the Privacy tab is rendered by mounting
		// `<SettingsPage page="settingsPrivacy" />` directly — the
		// top-of-page SegmentedControl tab UI has been removed (the
		// tabs now live in the sidebar as a nested submenu).
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);

		await waitFor(() => {
			expect(screen.getByText(/Privacy/)).toBeTruthy();
		});
	}

	it("renders the Reset Accessibility Permission button on macOS and calls reset_macos_accessibility", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: macUA,
			configurable: true,
		});
		try {
			await renderSettingsOnPrivacyTabForResetA11y();

			const button = await waitFor(() =>
				screen.getByRole("button", {
					name: "Reset macOS accessibility permission",
				}),
			);

			// Pin the glyph (ShieldBanIcon = shield + ban, distinct from
			// the benign reload/back icons used by the other buttons).
			const icon = button.querySelector(
				'[data-testid="hugeicon"]',
			) as HTMLElement | null;
			expect(icon?.getAttribute("data-name")).toBe("ShieldBanIcon");

			fireEvent.click(button);
			expect(mockCall).toHaveBeenCalledWith("reset_macos_accessibility");
		} finally {
			Object.defineProperty(window.navigator, "userAgent", {
				value: linuxUA,
				configurable: true,
			});
		}
	});

	// ── Finding #919 part (b): stale-grant reset suggestion ────────────
	// The Troubleshooting section probes `check_accessibility` on mount
	// (macOS only); on a CONFIRMED stale grant the backend echoes the
	// runtime `tccutil` command, which is surfaced next to the Reset
	// button. A missing / falsy suggestion must render nothing extra.
	it("surfaces the runtime tccutil reset command when the grant looks stale", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: macUA,
			configurable: true,
		});
		try {
			mockCall.mockImplementation((type: string) => {
				if (type === "get_config") return Promise.resolve(baseConfig);
				if (type === "set_config") return Promise.resolve({ success: true });
				if (type === "check_accessibility")
					return Promise.resolve({
						granted: false,
						platform: "macos",
						suggest_reset: true,
						reset_command: "tccutil reset Accessibility com.voicetyper.desktop",
					});
				return Promise.resolve({});
			});

			const { default: SettingsPage } = await import("@/pages/Settings");
			// Mount directly on Privacy (ADR-0021).
			renderWithProviders(<SettingsPage page="settingsPrivacy" />);

			await waitFor(() => {
				expect(screen.getByText(/Privacy/)).toBeTruthy();
			});

			// The suggested command must be rendered as code text.
			await waitFor(() => {
				expect(
					screen.getByText(
						"tccutil reset Accessibility com.voicetyper.desktop",
					),
				).toBeTruthy();
			});
			expect(mockCall).toHaveBeenCalledWith("check_accessibility");
		} finally {
			mockCall.mockImplementation((type: string) => {
				if (type === "get_config") return Promise.resolve(baseConfig);
				if (type === "set_config") return Promise.resolve({ success: true });
				return Promise.resolve({});
			});
			Object.defineProperty(window.navigator, "userAgent", {
				value: linuxUA,
				configurable: true,
			});
		}
	});

	// ── Finding #127 part (b) Linux sibling: "Reset Linux Permission" ──
	// A stale polkit authorization is cleared by restarting the polkit
	// daemon (pkexec) so the next "Grant permission" re-prompts.
	it("renders the Reset Linux Permission button on Linux and calls reset_linux_permissions", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: linuxUA,
			configurable: true,
		});
		try {
			await renderSettingsOnPrivacyTabForResetA11y();

			const button = await waitFor(() =>
				screen.getByRole("button", {
					name: "Reset Linux keyboard permission",
				}),
			);

			// Same glyph family as the macOS reset (both are
			// permission-reset actions; the UA gate makes them mutually
			// exclusive).
			const icon = button.querySelector(
				'[data-testid="hugeicon"]',
			) as HTMLElement | null;
			expect(icon?.getAttribute("data-name")).toBe("ShieldBanIcon");

			fireEvent.click(button);
			expect(mockCall).toHaveBeenCalledWith("reset_linux_permissions");
		} finally {
			Object.defineProperty(window.navigator, "userAgent", {
				value: macUA,
				configurable: true,
			});
		}
	});

	it("does NOT render either platform reset button on non-macOS/non-Linux", async () => {
		Object.defineProperty(window.navigator, "userAgent", {
			value: windowsUA,
			configurable: true,
		});
		await renderSettingsOnPrivacyTabForResetA11y();

		// Wait for the section to mount (via a sibling button).
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Re-run setup wizard" }),
			).toBeTruthy(),
		);

		expect(
			screen.queryByRole("button", {
				name: "Reset macOS accessibility permission",
			}),
		).toBeNull();
		expect(
			screen.queryByRole("button", {
				name: "Reset Linux keyboard permission",
			}),
		).toBeNull();
	});
});
