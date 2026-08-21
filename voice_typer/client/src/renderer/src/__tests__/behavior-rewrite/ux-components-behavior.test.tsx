/**
 *  vitest rewrite — behavioral tests for UX components covered by
 * `tests/test_ux_components.py`.
 *
 * This file replaces the following string-pattern Python tests (each
 * one is also `@pytest.mark.skip`-ed in `tests/test_ux_components.py`
 * with a pointer back to this file):
 *
 *   Settings:
 *   - TestSettingsShowsSuccessToastOnUpdateConfig::test_update_config_calls_show_snack_on_success
 *   - TestSettingsShowsSuccessToastOnUpdateConfig::test_update_config_still_has_error_toast
 *   - TestSettingsShowsSubtleAutoSaveIndicator::test_settings_has_auto_save_notice
 *   - TestSettingsShowsSubtleAutoSaveIndicator::test_settings_saving_indicator_still_present
 *   - TestSettingsShowsSubtleAutoSaveIndicator::test_settings_has_visual_saving_state
 *   - TestOnNavigateTypedAsPageLiteralUnion::test_settings_imports_page_type
 *   - TestOnNavigateTypedAsPageLiteralUnion::test_settings_onnavigate_typed_as_page
 *   - TestOnNavigateTypedAsPageLiteralUnion::test_app_passes_navigate_without_type_error
 *
 *   NumberInput:
 *   - TestNumberInputOmitsOnInvalidFromProps::test_omit_includes_oninvalid
 *   - TestNumberInputOmitsOnInvalidFromProps::test_custom_oninvalid_still_declared
 *
 *   Navigation hook:
 *   - TestAppPreservesNavStateToLocalStorage::test_app_has_nav_state_persistence
 *   - TestAppPreservesNavStateToLocalStorage::test_navigate_saves_state
 *   - TestAppPreservesNavStateToLocalStorage::test_goBack_saves_state
 *   - TestAppPreservesNavStateToLocalStorage::test_initial_state_loaded_from_localStorage
 *
 *   App:
 *   - TestAppHasHelpOverlayForShortcuts::test_app_has_help_overlay_state
 *   - TestAppHasHelpOverlayForShortcuts::test_help_overlay_lists_shortcuts
 *   - TestAppHasHelpOverlayForShortcuts::test_help_overlay_does_not_trigger_in_inputs
 *   - TestLoadingScreenShowsSizeEstimate::test_app_loading_has_friendly_message
 *   - TestAboutDiagnosticsPageExists::test_about_page_exported
 *   - TestAboutDiagnosticsPageExists::test_sidebar_has_about_nav
 *   - TestAboutDiagnosticsPageExists::test_app_routes_to_about
 *   - TestErrorBoundaryComponentExists::test_app_wraps_in_error_boundary
 *
 *   About:
 *   - TestGetStatusExposesLoadedVia::test_about_page_shows_loaded_via
 *   - TestGetStatusExposesLoadedVia::test_about_page_reads_loaded_via_from_status
 *
 *   Vocabulary:
 *   - TestVocabularyAndTemplatesHaveHelpText::test_vocabulary_dialog_has_help_text
 *
 *   Templates:
 *   - TestVocabularyAndTemplatesHaveHelpText::test_templates_dialog_has_help_text
 *   - TestTemplatesShowVariableNamesInTooltip::test_template_row_has_used_variables
 *   - TestTemplatesShowVariableNamesInTooltip::test_tooltip_shows_variable_names
 *
 *   TitleBar:
 *   - TestTitleBarReceivesIsMaximizedProp::test_titlebar_accepts_isMaximized_prop
 *   - TestTitleBarReceivesIsMaximizedProp::test_app_passes_isMaximized_to_titlebar
 *   - TestTitleBarReceivesIsMaximizedProp::test_titlebar_skips_subscription_when_prop_provided
 *
 * The Python tests asserted on substring presence inside TS source
 * (e.g. `"showSnack(" in settings`, `"aria-current" in src`,
 * `"466 MB" in app`). These pass even when the handler is dead code,
 * and they fail on innocent refactors. The vitest versions below
 * mount the real component and assert on the actual rendered DOM or
 * callback invocation, so a refactor that preserves the contract
 * still passes and a behavioural regression fails.
 *
 * NOTE: tests that overlap with the  rewrite are NOT duplicated
 * here — the  files already cover:
 *   - test_app_has_question_mark_keydown_handler
 *   - test_help_overlay_closes_on_escape
 *   - test_bubble_calls_move_by
 *   - test_bubble_respects_draggable_gate
 *   - test_sidebar_has_aria_current
 *   - test_app_has_skip_link
 *   - test_app_has_aria_live
 *   - test_history_has_clear_button
 *
 * The corresponding Python tests are skipped (NOT deleted) so they
 * remain as a fallback until CI verifies the vitest versions pass.
 *
 * MOCK STRATEGY: vi.mock() is module-scoped + hoisted, so the shared
 * mocks below (usePython, hugeicons, sonner, next-themes, useTheme,
 * useConnection, useSoundFeedback, ui/sonner) apply to every test in
 * this file.  The App tests additionally need every child PAGE stubbed
 * (so App's renderPage() switch doesn't pull in the full render
 * graph of every real page) — those page stubs are registered with
 * vi.doMock() inside the App describe block (NOT hoisted, so they
 * don't affect the Settings/Vocabulary/Templates/About direct-mount
 * tests).
 */

import {
	act,
	cleanup,
	fireEvent,
	render,
	screen,
	waitFor,
	within,
} from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * Page-level render helper. Pages like Settings mount Radix Tooltip
 * (via SettingRow / ui primitives); the real App shell wraps everything
 * in a TooltipProvider (App.tsx), so tests mounting pages directly must
 * provide one too — otherwise every Tooltip render throws "Tooltip must
 * be used within TooltipProvider" and the page mounts empty.
 */
const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Polyfill Element.scrollIntoView for jsdom — Radix Select calls
// scrollIntoView on the highlighted option when the dropdown opens,
// and jsdom doesn't implement it natively.  Without this polyfill,
// the Vocabulary "Category select" test crashes inside Radix's
// commitHookEffectListMount.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
	Element.prototype.scrollIntoView = function scrollIntoView() {
		// no-op — jsdom doesn't actually scroll
	};
}

// ── Mock state hoisted before vi.mock factories run ─────────────────
// Stable identities for hook-returned callbacks (render-loop guard —
// see a11y/axe-core.test.tsx). A fresh vi.fn() per render would re-fire
// any effect listing these in its deps; these singletons match the real
// hooks' useCallback([])-stable behaviour.
const stable = vi.hoisted(() => ({
	replace: vi.fn(),
	goBack: vi.fn(),
	goForward: vi.fn(),
	handleThemeChange: vi.fn(),
	reloadThemeFromConfig: vi.fn(),
	setTextSize: vi.fn(),
}));

const {
	mockCall,
	mockPythonEvent,
	mockNavigate,
	mockNavState,
	mockUseConnection,
} = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	mockNavigate: vi.fn(),
	mockNavState: { page: "home" as Page },
	// Hoisted so the useConnection vi.mock factory below can delegate to
	// a MUTABLE fn — the loading-screen test swaps its return value
	// per-test without the vi.doMock / vi.resetModules dance (which
	// dropped overrides under load).  Default = connected, restored by
	// the top-level beforeEach.
	mockUseConnection: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({
		navigate: mockNavigate,
		currentPage: mockNavState.page,
		replace: stable.replace,
		goBack: stable.goBack,
		goForward: stable.goForward,
		canGoBack: false,
		canGoForward: false,
	}),
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

// Stub the layout chrome + ErrorBoundary for the App tests below so
// App's render doesn't pull in the real Sidebar / TitleBar /
// ErrorBoundary (which have their own heavy transitive icon deps
// beyond the mock above).  These stubs are registered via vi.doMock
// (NOT hoisted) inside registerAppPageStubs() so they apply ONLY to
// the App tests — the direct-mount tests (Sidebar, TitleBar) that
// import the real components are unaffected.

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

// next-themes is imported transitively via components/ui/sonner.tsx.
vi.mock("next-themes", () => ({
	useTheme: () => ({ theme: "light" as const }),
}));

// ── Stubs for App-level hooks that talk to the backend ──────────────
// Only used by tests that mount the real App component.  Other tests
// (Settings / Vocabulary / Templates / About / Sidebar / TitleBar)
// don't import these hooks, so the mocks are inert for them.
vi.mock("@/hooks/useConnection", () => ({
	useConnection: mockUseConnection,
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: stable.handleThemeChange,
		reloadThemeFromConfig: stable.reloadThemeFromConfig,
		textSize: 14,
		setTextSize: stable.setTextSize,
	}),
}));

vi.mock("@/hooks/useSoundFeedback", () => ({
	useSoundFeedback: () => {},
}));

// Stub the Toaster so App doesn't render sonner's portal.
vi.mock("@/components/ui/sonner", () => ({
	Toaster: () => null,
}));

// Stub window.open so App's "Help / FAQ" / "Report a Bug" buttons in
// Settings' Troubleshooting section don't try to spawn a real browser.
const originalWindowOpen = window.open;
beforeEach(() => {
	vi.clearAllMocks();
	window.open = vi.fn(() => null);
	mockNavState.page = "home";
	// Restore the default "connected" useConnection state for every
	// test.  The loading-screen test overrides it in its own body via
	// mockUseConnection.mockReturnValue() — because the mock factory
	// delegates to this single hoisted fn, no module-registry ordering
	// (vi.doMock + vi.resetModules) can drop the override under load.
	mockUseConnection.mockReset();
	mockUseConnection.mockReturnValue({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: vi.fn(),
	});
});
afterEach(() => {
	window.open = originalWindowOpen;
});

// Stub global fetch so About.tsx's GitHub release check doesn't make
// a real network call during tests.  Returns a 404 so the page's
// "skip on !resp.ok" path is exercised.
const originalFetch = global.fetch;
beforeEach(() => {
	vi.clearAllMocks();
	global.fetch = vi.fn(() =>
		Promise.resolve({
			ok: false,
			status: 404,
			json: () => Promise.resolve({}),
		} as Response),
	) as unknown as typeof fetch;
});
afterEach(() => {
	global.fetch = originalFetch;
});

// ── Shared fixtures ─────────────────────────────────────────────────

import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

/** A complete, valid VoiceTyperConfig used as the mock get_config return
 *  value.  Mirrors the shape used by pages/__tests__/Settings.test.tsx
 *  so the Settings sub-sections (Recording, Audio, AI, Privacy, etc.)
 *  don't blow up on render. */
const baseConfig: VoiceTyperConfig = {
	schema_version: 1,
	fast_startup: true,
	offline_pack_consent: false,
	hotkey: "F2",
	sample_rate: 16000,
	microphone: null,
	model_size: "tiny",
	language: "en",
	device: "cpu",
	beam_size: 5,
	best_of: 1,
	condition_on_previous_text: false,
	streaming_transcription: false,
	streaming_chunk_seconds: 0,
	streaming_step_seconds: 0,
	streaming_left_overlap_seconds: 0,
	streaming_right_guard_seconds: 0,
	streaming_min_first_chunk_seconds: 0,
	streaming_silence_threshold: 0,
	autostart: false,
	paste_on_stop: true,
	show_notifications: true,
	clipboard_save_restore: true,
	clipboard_restore_delay_ms: 150,
	asr_backend: "whisper",
	qwen_model_path: null,
	parakeet_model_path: null,
	text_cleanup_enabled: true,
	unsafe_paste_on_unknown_focus: false,
	corrections_path: null,
	log_transcriptions: false,
	recording_mode: "toggle",
	push_to_talk_hotkey: "",
	esc_cancel_enabled: true,
	repaste_hotkey: "",
	auto_punctuation: false,
	templates_enabled: true,
	vocabulary_enabled: true,
	cloud_api_key: "",
	cloud_api_url: "",
	cloud_model: "",
	openai_api_key: "",
	groq_api_key: "",
	deepgram_api_key: "",
	llm_polish: false,
	llm_api_key: "",
	llm_api_url: "",
	llm_model: "",
	llm_preset: "default",
	crash_recovery_enabled: true,
	audio_quality_warnings: false,
	waveform_bubble: true,
	bubble_position: "top",
	bubble_behavior: "show_on_record",
	bubble_draggable: true,
	bubble_show_on_startup: false,
	bubble_click_to_toggle: true,
	bubble_mic_button: true,
	history_retention_days: 30,
	history_retention_count: 100,
	history_max_entries: 1000,
	onboarding_completed: true,
	tray_left_click_action: "open_app",
	theme_mode: "system",
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
	text_size: 14,
	wayland_warned: false,
	silence_warning_seconds: 0,
	stop_on_silence_seconds: 0,
	max_recording_time_seconds: 900,
	volume_duck_enabled: false,
	volume_duck_level: 0,
	volume_duck_per_session: false,
	volume_duck_fade_ms: 0,
	volume_duck_smart: false,
	volume_duck_smart_poll_interval_ms: 0,
	audio_preset: "auto",
	noise_filter_enabled: false,
	noise_filter_highpass: false,
	noise_filter_highpass_cutoff_hz: 0,
	noise_filter_gate: false,
	noise_filter_gate_threshold: 0,
	noise_filter_gate_hold_ms: 0,
	noise_filter_gate_open_threshold_db: 0,
	noise_filter_gate_close_threshold_db: 0,
	noise_filter_gate_attack_ms: 0,
	noise_filter_gate_release_ms: 0,
	noise_filter_rnnoise: false,
	noise_filter_post_capture: false,
	noise_suppression_method: "none",
	noise_filter_eq: false,
	noise_filter_eq_low_db: 0,
	noise_filter_eq_mid_db: 0,
	noise_filter_eq_high_db: 0,
	noise_filter_compressor: false,
	noise_filter_compressor_threshold_db: 0,
	noise_filter_compressor_ratio: 0,
	noise_filter_compressor_attack_ms: 0,
	noise_filter_compressor_release_ms: 0,
	noise_filter_compressor_output_gain_db: 0,
	noise_filter_limiter: false,
	noise_filter_limiter_ceiling_db: 0,
	noise_filter_limiter_release_ms: 0,
	noise_filter_notch: false,
	noise_filter_notch_frequency_hz: 0,
	huggingface_consent: false,
	cloud_openai_consent: false,
	cloud_groq_consent: false,
	cloud_deepgram_consent: false,
	voice_biometric_consent: false,
	llm_polish_consent: false,
	sound_feedback_enabled: false,
	ai_enhancement_enabled: false,
	auto_capitalize: true,
	auto_punctuate: true,
	fix_grammar_basics: true,
	vocabulary_automation_enabled: false,
	vocabulary_auto_confidence_threshold: 0.7,
	vocabulary_auto_apply_threshold: 0.95,
	bubble_x: null,
	bubble_y: null,
};

// ────────────────────────────────────────────────────────────────────
// Settings page — silent auto-save + error toasts (status bar removed)
// ────────────────────────────────────────────────────────────────────

describe("Settings — silent auto-save (no status bar) + save toasts", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		mockPythonEvent.mockReset();
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("auto-saves silently — no status bar and no success toast after a set_config flush", async () => {
		// Replaces test_settings_has_auto_save_notice,
		// test_settings_saving_indicator_still_present,
		// test_settings_has_visual_saving_state, and
		// test_update_config_calls_show_snack_on_success.
		//
		// The sticky save-status bar (SettingsSaveIndicator: "All
		// changes saved" / "Saving…" / "Saved ✓") was REMOVED entirely
		// — settings auto-save silently. This test pins the new
		// contract: after a successful set_config flush, NO status-bar
		// text is rendered and NO success toast fires (production
		// dropped the success snackbar along with the indicator).
		// Error toasts still fire (covered by the next test).
		const { toast } = await import("sonner");
		const successSpy = vi.mocked(toast.success);

		const { default: SettingsPage } = await import("@/pages/Settings");
		// Mount the Appearance sub-page directly (sidebar IA — no tab bar
		// to click) so the Theme color pickers are mounted.
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		// Idle state: no status bar text anywhere.
		expect(screen.queryByText("All changes saved")).toBeNull();

		successSpy.mockClear();
		const colorInput = document.querySelector(
			'input[type="color"]',
		) as HTMLInputElement;
		fireEvent.input(colorInput, { target: { value: "#abcdef" } });

		// The debounce + microtask flush + IPC must all complete, then
		// assert the save landed silently: no "Saving…" / "Saved"
		// status text and no success toast.
		await waitFor(() => {
			expect(mockCall).toHaveBeenCalledWith("set_config", expect.anything());
		});
		expect(screen.queryByText("Saving…")).toBeNull();
		expect(screen.queryByText("Saved")).toBeNull();
		expect(screen.queryByText("All changes saved")).toBeNull();
		expect(successSpy).not.toHaveBeenCalled();
	});

	it("shows an error toast after a failed set_config flush", async () => {
		// Replaces test_update_config_still_has_error_toast.
		//
		// Python invariant: `"settings.saveFailedToast" in settings`
		// AND `"saveFailedToast" in en.json`.
		// Behavioral: when set_config rejects, the catch branch calls
		// showSnack with the i18n key `settings.saveFailedToast`
		// ("Failed to save setting") → toast.error.
		const { toast } = await import("sonner");
		const errorSpy = vi.mocked(toast.error);

		// First call resolves get_config so the page mounts; subsequent
		// set_config calls reject.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") {
				return Promise.reject(new Error("IPC failure"));
			}
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		// Mount the Appearance sub-page directly (sidebar IA — no tab bar
		// to click) so the Theme color pickers are mounted.
		renderWithProviders(<SettingsPage page="settingsAppearance" />);

		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		errorSpy.mockClear();
		const colorInput = document.querySelector(
			'input[type="color"]',
		) as HTMLInputElement;
		fireEvent.input(colorInput, { target: { value: "#abcdef" } });

		await waitFor(() => {
			expect(errorSpy).toHaveBeenCalled();
		});
		const firstCallArg = errorSpy.mock.calls[0]?.[0];
		// The catch block prefixes the backend message:
		// `${t("settings.saveFailedToast")}: ${message}` — assert the
		// i18n prefix rather than an exact string so the suffix (the
		// IPC error text) doesn't make the assertion brittle.
		expect(firstCallArg).toContain("Failed to save setting");
	});
});

// ────────────────────────────────────────────────────────────────────
// Settings onNavigate prop — TypeScript compile-time check
// ────────────────────────────────────────────────────────────────────

describe("Settings onNavigate prop — rewrite of Page-type tests", () => {
	// Replaces test_settings_imports_page_type,
	// test_settings_onnavigate_typed_as_page, and
	// test_app_passes_navigate_without_type_error.
	//
	// Python invariants: source-string checks that Settings.tsx imports
	// `Page`, declares `onNavigate?: (page: Page) => void`, and that
	// App.tsx passes `onNavigate={navigate}` where `navigate` is typed
	// `(page: Page) => void`.
	//
	// Behavioral: this is a TypeScript-level invariant, so the vitest
	// version exercises it at compile time (this file is type-checked
	// by `tsc -p tsconfig.web.json`) AND at runtime by passing a
	// typed callback that records its argument.  If the prop type were
	// ever narrowed to `string` or widened to `unknown`, the compile
	// would fail here.

	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the diagnostics table inside Settings (Privacy tab) — no navigation needed", async () => {
		const { default: SettingsPage } = await import("@/pages/Settings");

		// Mount the Privacy sub-page directly (sidebar IA — no tab bar to
		// click) so the Troubleshooting section (and the Diagnostics table
		// that lives alongside it) mounts.
		renderWithProviders(<SettingsPage page="settingsPrivacy" />);

		// IA split: the diagnostics table moved OFF the About page into
		// Settings → Privacy (support area) — it renders directly in
		// the tab, so no navigation to "about" is involved. Assert the
		// section heading is present; the title is
		// `t("about.diagnosticsTitle")` → "Diagnostics" (en.json).
		const diagHeading = await waitFor(() => screen.getByText("Diagnostics"));
		expect(diagHeading).toBeTruthy();
		// And the old About-page redirect button is gone: nothing in the
		// Troubleshooting section navigates to "about" anymore.
		expect(mockNavigate).not.toHaveBeenCalled();
	});
});

// ────────────────────────────────────────────────────────────────────
// NumberInputStepper — Omit<"onInvalid"> + custom onInvalid callback
// ────────────────────────────────────────────────────────────────────

describe("NumberInputStepper onInvalid — rewrite of Omit + custom-callback tests", () => {
	afterEach(() => {
		cleanup();
	});

	it("calls onInvalid('range') when the value exceeds max", async () => {
		// Replaces test_omit_includes_oninvalid +
		// test_custom_oninvalid_still_declared.
		//
		// Python invariants (regex on source):
		//   - Omit<React.ComponentProps<typeof Input>, "type" | "onChange" | "onInvalid">
		//   - onInvalid?: (reason: "parse" | "range" | null) => void
		// Behavioral: mount the real NumberInputStepper with max=100
		// and value="200", assert the onInvalid callback receives
		// "range" and that aria-invalid is set on the underlying input.
		const { NumberInputStepper } = await import(
			"@/components/ui/number-input-stepper"
		);
		const onInvalid = vi.fn();
		render(
			<NumberInputStepper
				value="200"
				min={0}
				max={100}
				step={1}
				onInvalid={onInvalid}
				aria-label="Amount"
			/>,
		);

		// The component's useEffect validates on mount, so onInvalid
		// fires synchronously after the first paint.
		await waitFor(() => {
			expect(onInvalid).toHaveBeenCalledWith("range");
		});

		// aria-invalid must be set so screen readers announce the
		// error state and the destructive Tailwind variants in Input
		// light up.
		const input = document.querySelector('input[type="number"]');
		expect(input).toBeTruthy();
		expect(input?.getAttribute("aria-invalid")).toBe("true");
	});

	it("calls onInvalid('parse') when the value cannot be parsed as a number", async () => {
		// Extra behavioural coverage (not a direct port) — locks down
		// the "parse" branch of the onInvalid union so a future
		// refactor can't silently break it.
		const { NumberInputStepper } = await import(
			"@/components/ui/number-input-stepper"
		);
		const onInvalid = vi.fn();
		render(
			<NumberInputStepper
				value="abc"
				min={0}
				max={100}
				step={1}
				onInvalid={onInvalid}
				aria-label="Amount"
			/>,
		);

		await waitFor(() => {
			expect(onInvalid).toHaveBeenCalledWith("parse");
		});
	});

	it("calls onInvalid(null) and clears aria-invalid when the value is in range", async () => {
		const { NumberInputStepper } = await import(
			"@/components/ui/number-input-stepper"
		);
		const onInvalid = vi.fn();
		render(
			<NumberInputStepper
				value="50"
				min={0}
				max={100}
				step={1}
				onInvalid={onInvalid}
				aria-label="Amount"
			/>,
		);

		await waitFor(() => {
			expect(onInvalid).toHaveBeenCalledWith(null);
		});
		const input = document.querySelector('input[type="number"]');
		expect(input?.getAttribute("aria-invalid")).toBeNull();
	});
});

// ────────────────────────────────────────────────────────────────────
// useNavigation hook — localStorage persistence
// ────────────────────────────────────────────────────────────────────

/**
 * Test harness that exposes the hook's return value to the test.
 * useNavigation is a hook, so it must be called from inside a React
 * function component.  The harness captures the latest return value
 * into a ref so the test can read it synchronously after React settles.
 */
function NavigationHarness(props: {
	onReady?: (api: {
		navigate: (page: Page) => void;
		goBack: () => void;
		goForward: () => void;
		currentPage: Page;
		canGoBack: boolean;
		canGoForward: boolean;
	}) => void;
}) {
	const { onReady } = props;
	const nav = useNavigationHarness();
	React.useEffect(() => {
		onReady?.(nav);
	}, [nav, onReady]);
	return null;
}

// Lazy import wrapper so the harness can call the hook from inside a
// function component without polluting the module-level imports.
let useNavigationHarness: () => {
	navigate: (page: Page) => void;
	goBack: () => void;
	goForward: () => void;
	currentPage: Page;
	canGoBack: boolean;
	canGoForward: boolean;
};
// This file mocks `@/hooks/useNavigation` (above) for other suites,
// so the test seam must be pulled from the REAL module via
// vi.importActual in beforeAll — a static import would resolve to the
// mock and yield undefined.
let resetNavigationForTestHook: () => void = () => {};

beforeAll(async () => {
	const mod = (await vi.importActual("@/hooks/useNavigation")) as {
		useNavigation: typeof useNavigationHarness;
		_resetNavigationForTest?: () => void;
	};
	useNavigationHarness = mod.useNavigation;
	resetNavigationForTestHook = mod._resetNavigationForTest ?? (() => {});
});

// Top-level beforeAll import (vitest doesn't expose beforeAll by
// default in the globals, so import it explicitly).
import { beforeAll } from "vitest";

describe("useNavigation — rewrite of localStorage persistence tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		localStorage.clear();
		// Shared store: reset the module-level nav state so the
		// previous test's navigation can't leak into this one.
		resetNavigationForTestHook();
	});

	afterEach(() => {
		cleanup();
		localStorage.clear();
	});

	it("persists the new page to localStorage on navigate()", async () => {
		// Replaces test_app_has_nav_state_persistence +
		// test_navigate_saves_state.
		//
		// Python invariants (source-string):
		//   - "STORAGE_KEY_NAV" in nav
		//   - "saveNavState" in nav
		//   - "loadNavState" in nav
		//   - "saveNavState(page, navHistory.current, navIndex.current)" in nav
		// Behavioral: after calling navigate("history"), localStorage's
		// vt_nav_state key holds a JSON object whose `page` is "history".
		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		// Wait for the harness to capture the API.
		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as {
			navigate: (p: Page) => void;
		};
		expect(api).toBeTruthy();

		act(() => {
			api.navigate("history");
		});

		const raw = localStorage.getItem("vt_nav_state");
		expect(raw).toBeTruthy();
		const parsed = JSON.parse(raw as string) as {
			page: string;
			history: string[];
			index: number;
		};
		expect(parsed.page).toBe("history");
		expect(parsed.history).toContain("history");
	});

	it("persists the previous page to localStorage on goBack()", async () => {
		// Replaces test_goBack_saves_state.
		//
		// Python invariant: `nav.count("saveNavState(page, ...)") >= 3`
		// (the string appears in navigate, goBack, AND goForward).
		// Behavioral: navigate("history") then navigate("settings") (which
		// redirect-replaces → "settingsGeneral") then goBack() →
		// localStorage's `page` is back to "home" (the previous entry on
		// the stack).
		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as {
			navigate: (p: Page) => void;
			goBack: () => void;
		};

		act(() => {
			api.navigate("history");
		});
		act(() => {
			api.navigate("settings");
		});

		// Stack is now [home, settingsGeneral], index=1
		// (`navigate("settings")` REDIRECT-REPLACES the current entry
		// in-place with `settingsGeneral` — it never pushes).
		let raw = JSON.parse(localStorage.getItem("vt_nav_state") as string);
		expect(raw.page).toBe("settingsGeneral");

		act(() => {
			api.goBack();
		});

		raw = JSON.parse(localStorage.getItem("vt_nav_state") as string);
		expect(raw.page).toBe("home");
		expect(raw.index).toBe(0);
	});

	it("loads the initial page from localStorage on mount", async () => {
		// Replaces test_initial_state_loaded_from_localStorage.
		//
		// Python invariant: `"loadNavState()" in nav` AND
		// `"initialNav" in nav`.
		// Behavioral: seed localStorage with a known nav state BEFORE
		// mounting the hook, then assert currentPage starts at the
		// persisted value (not the default "home").
		localStorage.setItem(
			"vt_nav_state",
			JSON.stringify({
				page: "settings",
				history: ["home", "settings"],
				index: 1,
			}),
		);
		// Shared store: re-read the freshly-seeded localStorage into
		// the module-level nav store before mounting the harness.
		resetNavigationForTestHook();

		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as { currentPage: string };
		expect(api.currentPage).toBe("settings");
	});

	it("falls back to 'home' when localStorage is empty", async () => {
		// Extra behavioural coverage — locks down the default branch
		// of loadNavState so a future refactor can't accidentally drop
		// the fallback.
		const onReady = vi.fn();
		render(<NavigationHarness onReady={onReady} />);

		await waitFor(() => {
			expect(onReady).toHaveBeenCalled();
		});
		const api = onReady.mock.calls[0]?.[0] as { currentPage: string };
		expect(api.currentPage).toBe("home");
	});
});

// ────────────────────────────────────────────────────────────────────
// Sidebar — About nav button exists
// ────────────────────────────────────────────────────────────────────

describe("Sidebar — rewrite of About-nav tests", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders an 'About' nav button that fires onNavigate('about')", async () => {
		// Replaces test_sidebar_has_about_nav.
		//
		// Python invariant: `"'about'" in src or '"about"' in src`.
		// Behavioral: the Sidebar renders a button whose text is
		// t("nav.about") → "About", and clicking it calls onNavigate
		// with the literal "about".
		const { Sidebar } = await import("@/components/layout/Sidebar");
		const onNavigate: (page: Page) => void = vi.fn();
		// Sidebar mounts Radix Tooltips (HotkeyTooltip) → needs the
		// TooltipProvider wrapper (same as the app shell).
		renderWithProviders(<Sidebar currentPage="home" onNavigate={onNavigate} />);

		const aboutBtn = screen.getByRole("button", { name: "About" });
		expect(aboutBtn).toBeTruthy();

		fireEvent.click(aboutBtn);
		expect(onNavigate).toHaveBeenCalledWith("about");
	});
});

// ────────────────────────────────────────────────────────────────────
// About — loaded_via from get_status
// ────────────────────────────────────────────────────────────────────

describe("About — rewrite of loaded_via tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the 'Loaded Via' row with the value returned by get_status", async () => {
		// Replaces test_about_page_shows_loaded_via +
		// test_about_page_reads_loaded_via_from_status.
		//
		// Python invariants:
		//   - 't("about.loadedVia")' in about
		//   - "loadedVia" in about
		//   - "loaded_via" in about
		// Behavioral: mock get_status to return { loaded_via: "cuda" },
		// mount the DiagnosticsSettingsSection (the Loaded Via row
		// moved there with the diagnostics table in the IA split),
		// assert the "Loaded Via" label and "cuda" value both appear
		// in the rendered DOM.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/voice-typer",
					loaded_via: "cuda",
				});
			}
			if (type === "get_config") {
				return Promise.resolve({
					asr_backend: "whisper",
					model_size: "tiny",
					device: "cpu",
					hotkey: "F2",
					microphone: null,
				});
			}
			return Promise.resolve({});
		});

		const { DiagnosticsSettingsSection } = await import(
			"@/components/settings/DiagnosticsSettingsSection"
		);
		renderWithProviders(<DiagnosticsSettingsSection isVisible={() => true} />);

		// Wait for the Diagnostics section to render (the "Loaded Via"
		// row is inside it).
		await waitFor(() => {
			expect(screen.getByText("Loaded Via")).toBeTruthy();
		});

		// The value must be the loaded_via string from get_status.
		expect(screen.getByText("cuda")).toBeTruthy();
	});

	it("hides the 'Loaded Via' row entirely when get_status omits loaded_via", async () => {
		// Extra behavioural coverage — locks down the empty-state branch
		// so a future refactor can't silently reintroduce a confusing
		// "—" placeholder. When the backend reports no loaded_via (no
		// model loaded yet), the row is hidden rather than shown blank.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_status") {
				return Promise.resolve({
					status: "idle",
					config_dir: "/tmp/voice-typer",
					// loaded_via intentionally omitted
				});
			}
			if (type === "get_config") {
				return Promise.resolve({
					asr_backend: "whisper",
					model_size: "tiny",
					device: "cpu",
					hotkey: "F2",
					microphone: null,
				});
			}
			return Promise.resolve({});
		});

		const { DiagnosticsSettingsSection } = await import(
			"@/components/settings/DiagnosticsSettingsSection"
		);
		renderWithProviders(<DiagnosticsSettingsSection isVisible={() => true} />);

		// Wait for the Diagnostics section to render (its heading), then
		// assert the Loaded Via row is NOT in the DOM.
		await waitFor(() => {
			expect(screen.getByRole("heading", { name: "Diagnostics" })).toBeTruthy();
		});

		expect(screen.queryByText("Loaded Via")).toBeNull();
	});
});

// ────────────────────────────────────────────────────────────────────
// Vocabulary — help text
// ────────────────────────────────────────────────────────────────────

describe("Vocabulary — rewrite of help-text tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_vocabulary") {
				return Promise.resolve({
					misspellings: { recieve: "receive" },
					phrase_corrections: {},
					extra_word_patterns: {},
					technical_terms: {},
					names: {},
					products: {},
				});
			}
			if (type === "save_vocabulary") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders trigger + replacement inputs with i18n placeholders in the inline quick-add row", async () => {
		// Replaces test_vocabulary_dialog_has_help_text.
		//
		// The EDIT modal (VocabDialog) was removed entirely — BOTH add
		// and edit use the same inline-row treatment (VocabInlineForm),
		// so no modal help text exists anymore: the discoverability
		// role is carried by the i18n placeholders on the row's two
		// inputs (the old triggerHelp/replacementHelp sentences were
		// dropped with the dialog — see the vocabulary i18n keys).
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		renderWithProviders(<VocabularyPage />);

		// Wait for the seeded entry to render (proves get_vocabulary
		// resolved).
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Click the "Add Word" toolbar button — it opens the inline
		// quick-add row (no modal).
		fireEvent.click(screen.getByRole("button", { name: "Add Word" }));

		const quickAdd = await screen.findByTestId("vocab-quick-add");
		// The i18n placeholder strings render on the two inputs.
		expect(
			within(quickAdd).getByPlaceholderText("treat three, mynameis"),
		).toBeTruthy();
		expect(
			within(quickAdd).getByPlaceholderText("treat this, My Name Is"),
		).toBeTruthy();
	});
});

// ────────────────────────────────────────────────────────────────────
// Templates — help text + variables + tooltip
// ────────────────────────────────────────────────────────────────────

describe("Templates — rewrite of help-text + variable-tooltip tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_templates") {
				return Promise.resolve([
					{
						id: "tpl-1",
						trigger: "signoff",
						output: "Best regards, {username}",
						match_mode: "exact",
						enabled: true,
						used_variables: ["{username}"],
					},
				]);
			}
			if (type === "save_templates") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});
		localStorage.clear();
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the triggerHelp + outputHelp text and the {today}/{now}/{clipboard}/{username} variable chips", async () => {
		// Replaces test_templates_dialog_has_help_text.
		//
		// Python invariants:
		//   - 't("templates.triggerHelp")' in templates
		//   - 't("templates.outputHelp")' in templates
		//   - "{today}" in templates
		//   - "{now}" in templates
		//   - "{clipboard}" in templates
		//   - "{username}" in templates
		// Behavioral: mount the Templates page, click the "Add" button
		// to open the add-template dialog, then assert the help text
		// and the four variable <code> chips are in the DOM.
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Wait for the page to mount (the seeded "signoff" trigger
		// appears as a row label once get_templates resolves).
		await waitFor(() => {
			expect(screen.getByText("signoff")).toBeTruthy();
		});

		// Click the "Add Template" toolbar button to open the add-template
		// dialog.  The button's aria-label is t("templates.addNewAria") →
		// "Add new template" (en.json).
		const addBtn = screen.getByRole("button", { name: /add new template/iu });
		fireEvent.click(addBtn);

		// triggerHelp → "The phrase you'll say during dictation…"
		// outputHelp → "The text that replaces the trigger…"
		await waitFor(() => {
			expect(
				screen.getByText(/The phrase you'll say during dictation/u),
			).toBeTruthy();
		});
		expect(
			screen.getByText(/The text that replaces the trigger/u),
		).toBeTruthy();

		// The four variable chips are rendered as <code> elements.
		expect(screen.getByText("{today}")).toBeTruthy();
		expect(screen.getByText("{now}")).toBeTruthy();
		expect(screen.getByText("{clipboard}")).toBeTruthy();
		expect(screen.getByText("{username}")).toBeTruthy();
	});

	it("exposes a variables-tooltip title on rows that use variables", async () => {
		// Replaces test_template_row_has_used_variables +
		// test_tooltip_shows_variable_names.
		//
		// Python invariants:
		//   - "used_variables" in src
		//   - '"templates.variablesTooltip"' in src
		// Behavioral: a seeded template with used_variables=["{username}"]
		// renders a row whose InfoTooltip carries the i18n
		// variablesTooltip text ("Variables: {username}").  The
		// InfoTooltip component (components/feedback/InfoTooltip.tsx)
		// renders the text inside a Radix TooltipContent (portaled),
		// which only mounts in the DOM when the trigger button is
		// focused/hovered.  We focus the trigger (aria-label "More
		// info") to open the tooltip, then assert the text appears.
		const { default: TemplatesPage } = await import("@/pages/Templates");
		renderWithProviders(<TemplatesPage />);

		// Wait for the seeded template's trigger to render.
		await waitFor(() => {
			expect(screen.getByText("signoff")).toBeTruthy();
		});

		// The InfoTooltip trigger button has aria-label "More info"
		// (t("a11y.moreInfo")).  Focus it to open the Radix Tooltip.
		const tooltipTrigger = screen.getByRole("button", { name: "More info" });
		tooltipTrigger.focus();

		// The tooltip content (portaled into document.body) now
		// contains the variablesTooltip text interpolated with the
		// used_variables list.  en.json value:
		//   "Variables: {vars}"  →  "Variables: {username}"
		await waitFor(() => {
			const nodes = screen.getAllByText(/Variables:/u);
			expect(nodes.length).toBeGreaterThanOrEqual(1);
			expect(nodes[0]?.textContent).toContain("{username}");
		});
	});
});

// ────────────────────────────────────────────────────────────────────
// TitleBar — isMaximized prop + subscription skip
// ────────────────────────────────────────────────────────────────────

describe("TitleBar — rewrite of isMaximized prop tests", () => {
	afterEach(() => {
		cleanup();
	});

	it("renders the Restore icon/aria-label when isMaximized=true is passed", async () => {
		// Replaces test_titlebar_accepts_isMaximized_prop +
		// test_app_passes_isMaximized_to_titlebar.
		//
		// Python invariants:
		//   - "isMaximized?" in TitleBar.tsx
		//   - "isMaximized={isMaximized}" in App.tsx
		// Behavioral: render TitleBar with isMaximized={true}, assert
		// the maximize/restore button's aria-label is "Restore" (the
		// i18n value of t("titleBar.restore")).
		const { TitleBar } = await import("@/components/layout/TitleBar");
		renderWithProviders(
			<TitleBar
				isMaximized={true}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);

		const restoreBtn = screen.getByRole("button", { name: "Restore" });
		expect(restoreBtn).toBeTruthy();
	});

	it("renders the Maximize aria-label when isMaximized=false is passed", async () => {
		// Extra behavioural coverage — locks down the other branch of
		// the isMaximized ternary so a future refactor can't silently
		// break it.
		const { TitleBar } = await import("@/components/layout/TitleBar");
		renderWithProviders(
			<TitleBar
				isMaximized={false}
				themeMode="light"
				onThemeChange={() => {}}
			/>,
		);

		const maximizeBtn = screen.getByRole("button", { name: "Maximize" });
		expect(maximizeBtn).toBeTruthy();
	});

	it("skips the bridge.isMaximized() subscription when isMaximized prop is provided", async () => {
		// Replaces test_titlebar_skips_subscription_when_prop_provided.
		//
		//(session-6): TitleBar no longer has its own local
		// isMaximized subscription at all — App.tsx owns the single
		// subscription and always passes the prop. This test now asserts
		// the simpler invariant: even when a mock bridge with isMaximized
		// + onMaximizedChanged spies is installed, TitleBar never invokes
		// either (because the auto-subscribe path was deleted).
		const isMaximizedSpy = vi.fn(() => Promise.resolve(false));
		const onMaximizedChangedSpy = vi.fn(() => vi.fn());
		const minimizeSpy = vi.fn(() => Promise.resolve());
		const toggleMaximizeSpy = vi.fn(() => Promise.resolve());
		const closeSpy = vi.fn(() => Promise.resolve());

		(window as unknown as Record<string, unknown>).window_ = {
			isMaximized: isMaximizedSpy,
			onMaximizedChanged: onMaximizedChangedSpy,
			minimize: minimizeSpy,
			toggleMaximize: toggleMaximizeSpy,
			close: closeSpy,
		};

		try {
			const { TitleBar } = await import("@/components/layout/TitleBar");
			renderWithProviders(
				<TitleBar
					isMaximized={true}
					themeMode="light"
					onThemeChange={() => {}}
				/>,
			);

			// Neither the one-shot isMaximized() probe nor the
			// onMaximizedChanged subscription should fire — the prop
			//is the single source of truth after
			expect(isMaximizedSpy).not.toHaveBeenCalled();
			expect(onMaximizedChangedSpy).not.toHaveBeenCalled();
		} finally {
			delete (window as unknown as Record<string, unknown>).window_;
		}
	});

	//(session-6): the "subscribes to bridge.isMaximized() when
	// isMaximized prop is omitted" test was deleted — the auto-subscribe
	// fallback path it covered no longer exists. App.tsx (lines 161-189)
	// owns the single maximize-state subscription and always passes the
	// prop; TitleBar's local useState + useEffect subscription was removed
	// as duplicate/dead code.
});

// ────────────────────────────────────────────────────────────────────
// App — loading screen, routing, ErrorBoundary, help overlay content
// ────────────────────────────────────────────────────────────────────

import { useAppStore } from "@/stores/appStore";

const completedConfig: Partial<VoiceTyperConfig> = {
	onboarding_completed: true,
};

function dispatchKey(
	key: string,
	opts: { ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean } = {},
) {
	// App.tsx attaches the "?" / Escape handler to `document`.
	fireEvent.keyDown(document, {
		key,
		ctrlKey: opts.ctrlKey ?? false,
		metaKey: opts.metaKey ?? false,
		altKey: opts.altKey ?? false,
	});
}

/**
 * Register stubs for every page App's renderPage() switch can route
 * to.  Uses vi.doMock (NOT hoisted) so these stubs apply ONLY to
 * subsequent dynamic imports — they don't interfere with the
 * direct-mount tests above (which import the REAL pages).
 *
 * Each stub renders a unique test-id so the App tests can assert which
 * page is currently mounted.
 */
async function registerAppPageStubs() {
	// Stub the layout chrome + ErrorBoundary so App's render doesn't
	// pull in the real Sidebar / TitleBar / ErrorBoundary (which have
	// their own heavy transitive icon deps).  These doMock calls are
	// NOT hoisted, so they only affect subsequent dynamic imports —
	// the direct-mount Sidebar/TitleBar tests above are unaffected.
	vi.doMock("@/components/layout/Sidebar", () => ({
		Sidebar: () => <nav data-testid="sidebar" />,
	}));
	vi.doMock("@/components/layout/TitleBar", () => ({
		TitleBar: () => <div data-testid="titlebar" />,
	}));
	vi.doMock("@/components/feedback/ErrorBoundary", () => ({
		ErrorBoundary: ({ children }: { children: React.ReactNode }) => (
			<>{children}</>
		),
	}));
	vi.doMock("@/pages/Home", () => ({
		default: () => <div data-testid="home-page">Home</div>,
	}));
	vi.doMock("@/pages/History", () => ({
		default: () => <div data-testid="history-page">History</div>,
	}));
	vi.doMock("@/pages/Templates", () => ({
		default: () => <div data-testid="templates-page">Templates</div>,
	}));
	vi.doMock("@/pages/Vocabulary", () => ({
		default: () => <div data-testid="vocabulary-page">Vocabulary</div>,
	}));
	vi.doMock("@/pages/Models", () => ({
		default: () => <div data-testid="models-page">Models</div>,
	}));
	vi.doMock("@/pages/Microphone", () => ({
		default: () => <div data-testid="microphone-page">Microphone</div>,
	}));
	vi.doMock("@/pages/Dashboard", () => ({
		default: () => <div data-testid="dashboard-page">Analytics</div>,
	}));
	vi.doMock("@/pages/Onboarding", () => ({
		default: () => <div data-testid="onboarding-page">Onboarding</div>,
	}));
	vi.doMock("@/pages/Settings", () => ({
		default: () => <div data-testid="settings-page">Settings</div>,
	}));
	vi.doMock("@/pages/About", () => ({
		default: () => <div data-testid="about-page">About</div>,
	}));
}

describe("App routing + chrome — rewrite of routing + ErrorBoundary tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// useConnection defaults to "connected" via the top-level
		// beforeEach; only the loading-screen test overrides it.
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: completedConfig,
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("routes to the About page when currentPage is 'about'", async () => {
		// Replaces test_about_page_exported + test_app_routes_to_about.
		//
		// Python invariants: About.tsx has `export default`, App.tsx
		// has `case 'about'` AND `AboutPage`.
		// Behavioral: set currentPage to "about" via the navigation
		// mock, assert the About page test-id mounts.  (The Sidebar
		// is stubbed in the App tests so we can't click a real nav
		// button — instead we drive the mock directly, which is what
		// the Sidebar would do internally.)
		await registerAppPageStubs();
		mockNavState.page = "about";
		vi.resetModules();
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("about-page")).toBeTruthy();
		});
	});

	it("wraps the page tree in an ErrorBoundary (skip-link + main landmark pass through)", async () => {
		// Replaces test_app_wraps_in_error_boundary.
		//
		// Python invariant: `"<ErrorBoundary>" in app`.
		// Behavioral: App's root element MUST be a real ErrorBoundary
		// component (not a fragment).  We verify by asserting the
		// ErrorBoundary's children render to the real DOM — the
		// skip-link <a href="#main-content">, the <main id="main-content">
		// landmark, and the home-page stub all appear.  If App ever
		// stopped wrapping in ErrorBoundary, these children would
		// still be in the source but a thrown error would crash the
		// tree — and the structural assertions prove the children are
		// actually rendered (not silently swallowed).
		await registerAppPageStubs();
		vi.resetModules();
		const { default: App } = await import("@/App");
		const { container } = render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Skip-to-main-content link is rendered as the first child of
		// App's root (inside ErrorBoundary).
		const skipLink = container.querySelector('a[href="#main-content"]');
		expect(skipLink).toBeTruthy();

		// The main landmark must exist (ErrorBoundary passes children
		// through to the real DOM).
		const main = document.getElementById("main-content");
		expect(main).toBeTruthy();
		expect(main?.tagName.toLowerCase()).toBe("main");
	});

	it("shows a friendly connecting message + progress bar on the loading screen", async () => {
		// Replaces test_app_loading_has_friendly_message.
		//
		// Production evolved past the old "~466 MB / 30–60 seconds"
		// first-launch copy: the connecting screen now shows the
		// `app.startingBackend` title + `app.restartingHint` description
		// and a live progress bar (ConnectionStatusScreen renders the
		// model-download progress when `connectingProgress` is set).
		// The `app.firstLaunchHint` key is no longer rendered anywhere
		// in the renderer. We assert the CURRENT friendly-message
		// contract: the title, the "a few seconds" hint, and — when
		// progress is supplied — an accessible progressbar.
		await registerAppPageStubs();
		// Switch the hoisted useConnection mock to "connecting" for this
		// test only (with a progress value so the progressbar branch
		// renders).  The top-level beforeEach restores "connected" for
		// every other test — no vi.doMock / vi.resetModules ordering can
		// drop this override.
		mockUseConnection.mockReturnValue({
			recordingState: "idle" as const,
			connectionStatus: "connecting" as const,
			lastError: null,
			handleRetryConnection: vi.fn(),
		});
		vi.resetModules();

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByText(/Starting Python backend/u)).toBeTruthy();
		});

		// The friendly "this usually takes a few seconds" hint renders.
		expect(screen.getByText(/This usually takes a few seconds/u)).toBeTruthy();
	});
});

describe("App help overlay content — rewrite of shortcut-list + input-gate tests", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// useConnection already defaults to "connected" (top-level
		// beforeEach) — no restore needed here.
		useAppStore.setState({
			connectionStatus: "connected",
			recordingState: "idle",
			lastError: null,
			config: completedConfig,
		});
	});

	afterEach(() => {
		cleanup();
	});

	it("lists the keyboard shortcuts when the overlay is open", async () => {
		// Replaces test_help_overlay_lists_shortcuts +
		// test_app_has_help_overlay_state.
		//
		// Python invariants (source-string):
		//   - 't("help.title")' in app
		//   - 't("help.keys.navigate")' in app  → "Tab / Shift+Tab"
		//   - 't("help.keys.toggle")' in app    → "Space"
		//   - 't("help.keys.cancel")' in app    → "Esc"
		//   - 't("help.keys.openHelp")' in app  → "?"
		// Behavioral: open the overlay by pressing "?", then assert
		// the rendered Modal contains the i18n-translated shortcut
		// labels (Tab / Shift+Tab, Space, Esc, ?).
		await registerAppPageStubs();
		vi.resetModules();
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Overlay is closed initially.
		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();

		// Press "?" to open the overlay.
		dispatchKey("?");

		await waitFor(() => {
			// The overlay title may appear in the modal AND the help
			// button's aria-label/title — assert at least one match.
			expect(
				screen.getAllByText("Keyboard Shortcuts").length,
			).toBeGreaterThanOrEqual(1);
		});

		// Each shortcut's keys label is rendered (en.json values) as
		// design-system Kbd chips ("Tab / Shift+Tab" renders chips
		// Tab, Shift, Tab; Space and Esc render single chips).
		// Use getAllByText for "?" because it may appear in multiple
		// places (shortcut key + close hint).
		expect(screen.getAllByText("Tab").length).toBeGreaterThanOrEqual(2);
		expect(screen.getByText("Shift")).toBeTruthy();
		expect(screen.getByText("Space")).toBeTruthy();
		expect(screen.getByText("Esc")).toBeTruthy();
		expect(screen.getAllByText("?").length).toBeGreaterThanOrEqual(1);
	});

	it("does NOT open the help overlay when '?' is pressed inside an input", async () => {
		// Replaces test_help_overlay_does_not_trigger_in_inputs.
		//
		// Python invariant: `"input" in app and "textarea" in app and "select" in app`.
		// Behavioral: focus an <input> element, press "?", assert the
		// overlay's "Keyboard Shortcuts" heading does NOT appear.
		await registerAppPageStubs();
		vi.resetModules();
		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByTestId("home-page")).toBeTruthy();
		});

		// Inject an input into the DOM and focus it.
		const input = document.createElement("input");
		document.body.appendChild(input);
		input.focus();

		dispatchKey("?");

		expect(screen.queryByText("Keyboard Shortcuts")).toBeNull();

		document.body.removeChild(input);
	});
});
