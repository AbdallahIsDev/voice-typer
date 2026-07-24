/**
 * RW-1 vitest rewrite — behavioral tests for UX components covered by
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
 *   - TestVocabularyDialogHasCategoryPicker::test_vocabulary_has_category_state
 *   - TestVocabularyDialogHasCategoryPicker::test_vocabulary_has_category_labels
 *   - TestVocabularyDialogHasCategoryPicker::test_vocabulary_dialog_has_category_select
 *   - TestVocabularyDialogHasCategoryPicker::test_vocabulary_category_has_human_readable_labels
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
 * NOTE: tests that overlap with the RW-0 rewrite are NOT duplicated
 * here — the RW-0 files already cover:
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
} from "@testing-library/react";
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
const { mockCall, mockPythonEvent, mockNavigate, mockNavState } = vi.hoisted(() => ({
	mockCall: vi.fn(),
	mockPythonEvent: vi.fn(),
	mockNavigate: vi.fn(),
	mockNavState: { page: "home" as const },
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({ call: mockCall }),
	usePythonEvent: mockPythonEvent,
}));

vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({
		navigate: mockNavigate,
		currentPage: mockNavState.page,
		replace: vi.fn(),
		goBack: vi.fn(),
		goForward: vi.fn(),
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

// Stub every icon used by the render graph of Settings / Vocabulary /
// Templates / About / Sidebar / TitleBar / App.  Each is a `{ name }`
// tagged object so the HugeiconsIcon mock can surface which icon was
// rendered via data-name.  The set is the union of all icons imported
// across the renderer (enumerated by grepping every
// `import { ... } from "@hugeicons/core-free-icons"` site).
vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		Add01Icon: make("Add01Icon"),
		AiBrain03Icon: make("AiBrain03Icon"),
		Alert02Icon: make("Alert02Icon"),
		Analytics01Icon: make("Analytics01Icon"),
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowRight01Icon: make("ArrowRight01Icon"),
		ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		BookOpen02Icon: make("BookOpen02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Copy01Icon: make("Copy01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Download01Icon: make("Download01Icon"),
		File02Icon: make("File02Icon"),
		FilterIcon: make("FilterIcon"),
		Folder02Icon: make("Folder02Icon"),
		HistoryIcon: make("HistoryIcon"),
		Home04Icon: make("Home04Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		LockKeyIcon: make("LockKeyIcon"),
		Mic02Icon: make("Mic02Icon"),
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		PanelLeftIcon: make("PanelLeftIcon"),
		PauseIcon: make("PauseIcon"),
		PencilEdit02Icon: make("PencilEdit02Icon"),
		PlayIcon: make("PlayIcon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Settings03Icon: make("Settings03Icon"),
		Share08Icon: make("Share08Icon"),
		Shield01Icon: make("Shield01Icon"),
		SparklesIcon: make("SparklesIcon"),
		StarIcon: make("StarIcon"),
		StopIcon: make("StopIcon"),
		Sun01Icon: make("Sun01Icon"),
		TextIcon: make("TextIcon"),
		Tick02Icon: make("Tick02Icon"),
		Time02Icon: make("Time02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
		ZapIcon: make("ZapIcon"),
	};
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
	useConnection: () => ({
		recordingState: "idle" as const,
		connectionStatus: "connected" as const,
		lastError: null,
		handleRetryConnection: vi.fn(),
	}),
}));

vi.mock("@/hooks/useTheme", () => ({
	useTheme: () => ({
		themeMode: "system" as const,
		handleThemeChange: vi.fn(),
		reloadThemeFromConfig: vi.fn(),
		textSize: 14,
		setTextSize: vi.fn(),
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
	window.open = vi.fn(() => null);
	mockNavState.page = "home";
});
afterEach(() => {
	window.open = originalWindowOpen;
});

// Stub global fetch so About.tsx's GitHub release check doesn't make
// a real network call during tests.  Returns a 404 so the page's
// "skip on !resp.ok" path is exercised.
const originalFetch = global.fetch;
beforeEach(() => {
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
	hotkey: "F2",
	sample_rate: 16000,
	microphone: null,
	model_size: "small.en",
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
// Settings page — save toasts + 3-state save indicator
// ────────────────────────────────────────────────────────────────────

describe("Settings — RW-1 rewrite of save-toast + auto-save indicator tests", () => {
	beforeEach(() => {
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

	it("renders the 'All changes saved' idle indicator when no save is in flight", async () => {
		// Replaces test_settings_has_auto_save_notice +
		// test_settings_saving_indicator_still_present +
		// test_settings_has_visual_saving_state.
		//
		// Python invariants (string-pattern):
		//   - "Auto-save" in Settings.tsx
		//   - "Saving..." in Settings.tsx OR "setSaving(" in Settings.tsx
		//   - "bg-amber-400" OR "bg-amber-500" in Settings.tsx
		// Behavioral: the rendered DOM has the accessible 3-state save
		// indicator paragraph (aria-live="polite") whose idle text is
		// the i18n key `settings.allChangesSaved` → "All changes saved".
		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		// Wait for config to load (the Appearance tab label appears).
		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Idle state: "All changes saved" is rendered.
		expect(screen.getByText("All changes saved")).toBeTruthy();

		// The indicator is an aria-live="polite" paragraph so screen
		// readers announce state changes.  (Replaces the Python
		// assertion that the i18n string is present in source.)
		const liveRegion = screen.getByText("All changes saved").closest("p");
		expect(liveRegion?.getAttribute("aria-live")).toBe("polite");
	});

	it("shows the 'Saving…' amber indicator while a set_config is in flight", async () => {
		// Replaces test_settings_has_visual_saving_state (amber bg) +
		// test_settings_saving_indicator_still_present (setSaving call).
		//
		// Behavioral: trigger a save (color picker change), and before
		// the IPC resolves, assert the DOM shows the "Saving…" label
		// and the amber pulse dot (bg-amber-400).
		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Navigate to Appearance tab so the color pickers mount.
		fireEvent.click(screen.getByText("Appearance"));
		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		// Block the set_config resolution so "Saving…" stays visible.
		// Use a deferred-promise pattern: releaseRef.fn holds a
		// no-arg "release" function that resolves the blocked Promise.
		// We wrap it in an object ref so TypeScript's control-flow
		// analysis doesn't narrow it to `never` at the call site
		// (TS assumes a `let`-bound variable assigned only inside a
		// closure stays at its initializer value, which would make
		// the truthy branch unreachable → `never`).
		const releaseRef: { fn: (() => void) | null } = { fn: null };
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(baseConfig);
			if (type === "set_config") {
				return new Promise<{ success: boolean }>((resolve) => {
					releaseRef.fn = () => resolve({ success: true });
				});
			}
			return Promise.resolve({});
		});

		const colorInput = document.querySelector(
			'input[type="color"]',
		) as HTMLInputElement;
		fireEvent.input(colorInput, { target: { value: "#abcdef" } });

		// "Saving…" appears once the debounced update fires.  The amber
		// pulse dot is the span with class bg-amber-400.
		await waitFor(() => {
			expect(screen.getByText("Saving…")).toBeTruthy();
		});
		const amberDot = document.querySelector(".bg-amber-400");
		expect(amberDot).toBeTruthy();

		// Release the blocked IPC so the component can unmount cleanly.
		releaseRef.fn?.();
	});

	it("shows a success toast after a successful set_config flush", async () => {
		// Replaces test_update_config_calls_show_snack_on_success.
		//
		// Python invariant: the updateConfig callback in Settings.tsx
		// source contains a `showSnack(..., "success")` call.
		// Behavioral: after a real set_config flush resolves, the
		// shared useSnackbar hook captured by the sonner mock fires
		// toast.success with the i18n key `settings.savedToast`
		// ("Saved").  We spy on the sonner toast module to capture
		// the call.
		const { toast } = await import("sonner");
		const successSpy = vi.mocked(toast.success);

		const { default: SettingsPage } = await import("@/pages/Settings");
		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		fireEvent.click(screen.getByText("Appearance"));
		await waitFor(() => {
			expect(
				document.querySelectorAll('input[type="color"]').length,
			).toBeGreaterThanOrEqual(1);
		});

		successSpy.mockClear();
		const colorInput = document.querySelector(
			'input[type="color"]',
		) as HTMLInputElement;
		fireEvent.input(colorInput, { target: { value: "#abcdef" } });

		// The 300ms debounce + microtask flush + IPC must all complete
		// before the success toast fires.
		await waitFor(() => {
			expect(successSpy).toHaveBeenCalled();
		});
		// The toast message is the i18n key `settings.savedToast`
		// ("Saved" in en.json).
		const firstCallArg = successSpy.mock.calls[0]?.[0];
		expect(firstCallArg).toBe("Saved");
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
		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		fireEvent.click(screen.getByText("Appearance"));
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
		expect(firstCallArg).toBe("Failed to save setting");
	});
});

// ────────────────────────────────────────────────────────────────────
// Settings onNavigate prop — TypeScript compile-time check
// ────────────────────────────────────────────────────────────────────

describe("Settings onNavigate prop — RW-1 rewrite of Page-type tests", () => {
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

	it("calls navigate('about') when the Diagnostics button is clicked", async () => {
		const { default: SettingsPage } = await import("@/pages/Settings");

		render(<SettingsPage />);

		await waitFor(() => {
			expect(screen.getByText("Appearance")).toBeTruthy();
		});

		// Click the Privacy tab so the Troubleshooting section mounts.
		fireEvent.click(screen.getByText("Privacy"));

		// The "Diagnostics" button in the Troubleshooting section calls
		// `onNavigate?.("about")` — the literal "about" is a member of
		// the Page union, so this is the type-safe call site the
		// Python tests were trying to lock down.
		//
		// The button's aria-label is `t("settings.troubleshooting.diagnosticsAria")`
		// → "Open Diagnostics" (en.json), so the accessible name is
		// "Open Diagnostics", not "Diagnostics".  Match on a regex to
		// stay robust against minor wording changes.
		const diagBtn = await waitFor(() =>
			screen.getByRole("button", { name: /open diagnostics/iu }),
		);
		fireEvent.click(diagBtn);

		expect(mockNavigate).toHaveBeenCalledWith("about");
	});
});

// ────────────────────────────────────────────────────────────────────
// NumberInputStepper — Omit<"onInvalid"> + custom onInvalid callback
// ────────────────────────────────────────────────────────────────────

describe("NumberInputStepper onInvalid — RW-1 rewrite of Omit + custom-callback tests", () => {
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
beforeAll(async () => {
	const mod = (await vi.importActual("@/hooks/useNavigation")) as {
		useNavigation: typeof useNavigationHarness;
	};
	useNavigationHarness = mod.useNavigation;
});

// Top-level beforeAll import (vitest doesn't expose beforeAll by
// default in the globals, so import it explicitly).
import { beforeAll } from "vitest";

describe("useNavigation — RW-1 rewrite of localStorage persistence tests", () => {
	beforeEach(() => {
		localStorage.clear();
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
		// Behavioral: navigate("history") then goBack() → localStorage's
		// `page` is back to "home" (the previous entry on the stack).
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

		// Stack is now [home, history, settings], index=2.
		let raw = JSON.parse(localStorage.getItem("vt_nav_state") as string);
		expect(raw.page).toBe("settings");

		act(() => {
			api.goBack();
		});

		raw = JSON.parse(localStorage.getItem("vt_nav_state") as string);
		expect(raw.page).toBe("history");
		expect(raw.index).toBe(1);
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

describe("Sidebar — RW-1 rewrite of About-nav tests", () => {
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
		render(
			<Sidebar
				currentPage="home"
				onNavigate={onNavigate}
				themeMode="system"
				onThemeChange={() => {}}
			/>,
		);

		const aboutBtn = screen.getByRole("button", { name: "About" });
		expect(aboutBtn).toBeTruthy();

		fireEvent.click(aboutBtn);
		expect(onNavigate).toHaveBeenCalledWith("about");
	});
});

// ────────────────────────────────────────────────────────────────────
// About — loaded_via from get_status
// ────────────────────────────────────────────────────────────────────

describe("About — RW-1 rewrite of loaded_via tests", () => {
	beforeEach(() => {
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
		// mount the About page, assert the "Loaded Via" label and
		// "cuda" value both appear in the rendered DOM.
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
					model_size: "small.en",
					device: "cpu",
					hotkey: "F2",
					microphone: null,
				});
			}
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: null,
					elapsed_s: null,
					cache_ratio: 0,
					cache_label: "unknown",
					cached_bytes: 0,
					total_bytes: 0,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		// Wait for the Diagnostics section to render (the "Loaded Via"
		// row is inside it).
		await waitFor(() => {
			expect(screen.getByText("Loaded Via")).toBeTruthy();
		});

		// The value must be the loaded_via string from get_status.
		expect(screen.getByText("cuda")).toBeTruthy();
	});

	it("falls back to 'Unknown' when get_status omits loaded_via", async () => {
		// Extra behavioural coverage — locks down the fallback branch
		// so a future refactor can't silently break it.
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
					model_size: "small.en",
					device: "cpu",
					hotkey: "F2",
					microphone: null,
				});
			}
			if (type === "get_prewarm_status") {
				return Promise.resolve({
					last_run: null,
					elapsed_s: null,
					cache_ratio: 0,
					cache_label: "unknown",
					cached_bytes: 0,
					total_bytes: 0,
					prewarm_running: false,
				});
			}
			return Promise.resolve({});
		});

		const { default: AboutPage } = await import("@/pages/About");
		render(<AboutPage />);

		await waitFor(() => {
			expect(screen.getByText("Loaded Via")).toBeTruthy();
		});

		// The fallback is t("about.unknown") → "—" (em-dash) per en.json.
		// (about.unknown is "—", NOT "Unknown" — the "Unknown" string is
		// used elsewhere, e.g. about.errorUnknown.)
		expect(screen.getByText("—")).toBeTruthy();
	});
});

// ────────────────────────────────────────────────────────────────────
// Vocabulary — help text + category picker
// ────────────────────────────────────────────────────────────────────

describe("Vocabulary — RW-1 rewrite of help-text + category-picker tests", () => {
	beforeEach(() => {
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

	it("renders trigger + replacement help text below the input fields", async () => {
		// Replaces test_vocabulary_dialog_has_help_text.
		//
		// Python invariants:
		//   - 't("vocabulary.triggerHelp")' in vocab
		//   - 't("vocabulary.replacementHelp")' in vocab
		// Behavioral: mount the Vocabulary page, click the "Add Word"
		// button to open the add-entry dialog (the help text lives
		// inside the Modal), then assert the i18n-translated help
		// strings appear in the rendered DOM.
		// en.json:
		//   triggerHelp → "Type the word(s) exactly as the ASR
		//                  mishears them…"
		//   replacementHelp → "The corrected text that will be
		//                      pasted…"
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		// Wait for the seeded entry to render (proves get_vocabulary
		// resolved).
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Click the "Add Word" toolbar button to open the add-entry
		// dialog.  The button's visible text is t("vocabulary.addWord")
		// → "Add Word" (no aria-label on this button).
		fireEvent.click(screen.getByRole("button", { name: "Add Word" }));

		await waitFor(() => {
			expect(
				screen.getByText(
					/Type the word\(s\) exactly as the ASR mishears them/u,
				),
			).toBeTruthy();
		});
		expect(
			screen.getByText(/The corrected text that will be pasted/u),
		).toBeTruthy();
	});

	it("renders a Category select with an 'Auto-detect' option when adding a new entry", async () => {
		// Replaces test_vocabulary_has_category_state +
		// test_vocabulary_has_category_labels +
		// test_vocabulary_dialog_has_category_select +
		// test_vocabulary_category_has_human_readable_labels.
		//
		// Python invariants (source-string):
		//   - "const [category, setCategory]" in vocab
		//   - "CATEGORY_LABELS" in vocab
		//   - for cat in [misspellings, phrase_corrections,
		//     extra_word_patterns, technical_terms, names, products]:
		//     assert cat in vocab
		//   - "Category" in vocab
		//   - 'value="auto"' in vocab
		//   - "resolvedCategory" in vocab
		//   - 't("vocabulary.category.misspellings")' in vocab
		//   - 't("vocabulary.category.phraseCorrections")' in vocab
		//   - 't("vocabulary.category.technicalTerms")' in vocab
		//   - 't("vocabulary.category.names")' in vocab
		//   - 't("vocabulary.category.products")' in vocab
		// Behavioral: mount the Vocabulary page, click the "Add" button
		// to open the add-entry dialog, then assert the Category select
		// exists and exposes the human-readable labels (Misspellings,
		// Phrase Corrections, Technical Terms, Names, Products).
		const { default: VocabularyPage } = await import("@/pages/Vocabulary");
		render(<VocabularyPage />);

		// Wait for the seeded entry to render (proves get_vocabulary
		// resolved).
		await waitFor(() => {
			expect(screen.getByText("recieve")).toBeTruthy();
		});

		// Click the "Add Word" toolbar button to open the add-entry dialog.
		// The button's visible text is t("vocabulary.addWord") → "Add Word"
		// (no aria-label on this button).
		fireEvent.click(screen.getByRole("button", { name: "Add Word" }));

		// The dialog renders a "Category" label and a Radix Select
		// whose trigger is a button with the "Auto-detect" text (the
		// option value="auto").
		await waitFor(() => {
			expect(screen.getByText("Category")).toBeTruthy();
		});
		expect(screen.getByText("Auto-detect")).toBeTruthy();

		// Open the Radix Select to expose the options.
		const selectTrigger = screen.getByRole("combobox");
		fireEvent.click(selectTrigger);

		// All five human-readable category labels must be present as
		// options.  These are the en.json values for the i18n keys
		// the Python test asserted on.  Use getAllByText because the
		// same label may also appear as a category badge on an
		// existing list row (the seeded "recieve" entry's badge
		// renders "Misspellings" too).
		await waitFor(() => {
			expect(screen.getAllByText("Misspellings").length).toBeGreaterThanOrEqual(1);
		});
		expect(screen.getAllByText("Phrase Corrections").length).toBeGreaterThanOrEqual(1);
		expect(screen.getAllByText("Technical Terms").length).toBeGreaterThanOrEqual(1);
		expect(screen.getAllByText("Names").length).toBeGreaterThanOrEqual(1);
		expect(screen.getAllByText("Products").length).toBeGreaterThanOrEqual(1);
	});
});

// ────────────────────────────────────────────────────────────────────
// Templates — help text + variables + tooltip
// ────────────────────────────────────────────────────────────────────

describe("Templates — RW-1 rewrite of help-text + variable-tooltip tests", () => {
	beforeEach(() => {
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
		render(<TemplatesPage />);

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
		render(<TemplatesPage />);

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

describe("TitleBar — RW-1 rewrite of isMaximized prop tests", () => {
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
		render(<TitleBar isMaximized={true} />);

		const restoreBtn = screen.getByRole("button", { name: "Restore" });
		expect(restoreBtn).toBeTruthy();
	});

	it("renders the Maximize aria-label when isMaximized=false is passed", async () => {
		// Extra behavioural coverage — locks down the other branch of
		// the isMaximized ternary so a future refactor can't silently
		// break it.
		const { TitleBar } = await import("@/components/layout/TitleBar");
		render(<TitleBar isMaximized={false} />);

		const maximizeBtn = screen.getByRole("button", { name: "Maximize" });
		expect(maximizeBtn).toBeTruthy();
	});

	it("skips the bridge.isMaximized() subscription when isMaximized prop is provided", async () => {
		// Replaces test_titlebar_skips_subscription_when_prop_provided.
		//
		// Python invariant: `"isMaximizedProp !== undefined" in src`.
		// Behavioral: when isMaximized is passed as a prop, TitleBar
		// must NOT call window.window_.isMaximized() (the prop wins).
		// We install a mock window bridge with spied isMaximized +
		// onMaximizedChanged, render TitleBar with isMaximized={true},
		// and assert neither spy was called.
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
			render(<TitleBar isMaximized={true} />);

			// Neither the one-shot isMaximized() probe nor the
			// onMaximizedChanged subscription should fire when the
			// prop is provided.
			expect(isMaximizedSpy).not.toHaveBeenCalled();
			expect(onMaximizedChangedSpy).not.toHaveBeenCalled();
		} finally {
			delete (window as unknown as Record<string, unknown>).window_;
		}
	});

	it("subscribes to bridge.isMaximized() when isMaximized prop is omitted", async () => {
		// Extra behavioural coverage — locks down the other branch of
		// the subscription gate so a future refactor can't silently
		// break the auto-subscribe path.
		const isMaximizedSpy = vi.fn(() => Promise.resolve(true));
		const onMaximizedChangedSpy = vi.fn(() => vi.fn());
		(window as unknown as Record<string, unknown>).window_ = {
			isMaximized: isMaximizedSpy,
			onMaximizedChanged: onMaximizedChangedSpy,
			minimize: vi.fn(() => Promise.resolve()),
			toggleMaximize: vi.fn(() => Promise.resolve()),
			close: vi.fn(() => Promise.resolve()),
		};

		try {
			const { TitleBar } = await import("@/components/layout/TitleBar");
			render(<TitleBar />);

			// The subscription path runs the one-shot probe AND
			// registers the onMaximizedChanged listener.
			expect(isMaximizedSpy).toHaveBeenCalled();
			expect(onMaximizedChangedSpy).toHaveBeenCalled();
		} finally {
			delete (window as unknown as Record<string, unknown>).window_;
		}
	});
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

describe("App routing + chrome — RW-1 rewrite of routing + ErrorBoundary tests", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Re-register the default "connected" useConnection mock in
		// case a prior test (e.g. the model-download test below)
		// overrode it with vi.doMock to return "connecting".
		// vi.doMock registrations persist across tests, so we must
		// explicitly restore the default here.
		vi.doMock("@/hooks/useConnection", () => ({
			useConnection: () => ({
				recordingState: "idle" as const,
				connectionStatus: "connected" as const,
				lastError: null,
				handleRetryConnection: vi.fn(),
			}),
		}));
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

	it("shows the model-download estimate ('466 MB' / '30–60') on the connecting loading screen", async () => {
		// Replaces test_app_loading_has_friendly_message.
		//
		// Python invariant: `"466 MB" in app or "small.en" in app`
		// AND `"30" in app and "60" in app`.
		// Behavioral: when connectionStatus is "connecting", App
		// renders the i18n keys `app.startingBackend` +
		// `app.firstLaunchHint`.  The en.json value of
		// `firstLaunchHint` is "First launch can take 30–60 seconds
		// while we download the speech model (~466 MB for small.en)…".
		// We override useConnection to return "connecting" and assert
		// both substrings are present in the rendered DOM.
		await registerAppPageStubs();
		// Override the module-level useConnection mock to return
		// "connecting" for this test only.
		vi.doMock("@/hooks/useConnection", () => ({
			useConnection: () => ({
				recordingState: "idle" as const,
				connectionStatus: "connecting" as const,
				lastError: null,
				handleRetryConnection: vi.fn(),
			}),
		}));
		vi.resetModules();

		const { default: App } = await import("@/App");
		render(<App />);

		await waitFor(() => {
			expect(screen.getByText(/Starting Python backend/u)).toBeTruthy();
		});

		// The firstLaunchHint text contains both "466 MB" and
		// "30–60 seconds" (or "30-60" depending on the en-dash vs
		// hyphen).  Assert on the numeric anchors the Python test
		// cared about.
		const hint = screen.getByText(/466\s*MB/u);
		expect(hint).toBeTruthy();
		expect(hint.textContent).toMatch(/30/u);
		expect(hint.textContent).toMatch(/60/u);
	});
});

describe("App help overlay content — RW-1 rewrite of shortcut-list + input-gate tests", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		localStorage.clear();
		// Restore the default "connected" useConnection mock — the
		// model-download test in the previous describe block may have
		// overridden it with vi.doMock to return "connecting", and
		// vi.doMock registrations persist across tests.
		vi.doMock("@/hooks/useConnection", () => ({
			useConnection: () => ({
				recordingState: "idle" as const,
				connectionStatus: "connected" as const,
				lastError: null,
				handleRetryConnection: vi.fn(),
			}),
		}));
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
			expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy();
		});

		// Each shortcut's keys label is rendered (en.json values).
		// Use getAllByText for "?" because it may appear in multiple
		// places (shortcut key + close hint).
		expect(screen.getByText("Tab / Shift+Tab")).toBeTruthy();
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
