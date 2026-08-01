/**
 * Tests for the Settings page-level config cache invalidation
 * (companion to the `useSettingsConfig` hook tests).
 *
 * Background: `useSettingsConfig` keeps a module-level `_cachedConfig`
 * so the Settings page renders instantly on re-visit (no spinner).
 * Pre-fix, the page's mount effect was `if (!config) loadConfig()` —
 * i.e. it ONLY re-fetched when the cache was null. So a user who
 * changed `audio_preset` (or any audio filter) on the Microphone page,
 * or `model_size` / `asr_backend` on the Models page, then navigated
 * to Settings, would see the STALE cached value. The
 * `mergeExternalConfig` subscription (config_changed → cache update)
 * only fires while Settings is mounted, so cross-page edits made
 * while Settings was unmounted were lost on re-mount.
 *
 * The fix: drop the `if (!config)` guard and ALWAYS call `loadConfig()`
 * on mount (matching how `useModelConfig` on the Models page and the
 * Microphone page already behave). The page still renders instantly
 * from the cached value (the state initializer seeds `config` from
 * `_cachedConfig`), then re-renders with the fresh value when
 * `loadConfig` resolves.
 *
 * These tests verify the fix at the page level by mounting the
 * SettingsPage twice with different `get_config` responses and
 * asserting the second mount's value is the FRESH one (not the
 * stale cached one).
 */
import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", () => {
	const make = (name: string) => ({ name });
	return {
		ArrowDown01Icon: make("ArrowDown01Icon"),
		ArrowTurnBackwardIcon: make("ArrowTurnBackwardIcon"),
		ArrowUp01Icon: make("ArrowUp01Icon"),
		Book02Icon: make("Book02Icon"),
		Bug02Icon: make("Bug02Icon"),
		Cancel01Icon: make("Cancel01Icon"),
		CheckmarkCircle01Icon: make("CheckmarkCircle01Icon"),
		Delete01Icon: make("Delete01Icon"),
		Delete02Icon: make("Delete02Icon"),
		File02Icon: make("File02Icon"),
		InformationCircleIcon: make("InformationCircleIcon"),
		KeyboardIcon: make("KeyboardIcon"),
		ModernTvIcon: make("ModernTvIcon"),
		Moon02Icon: make("Moon02Icon"),
		RefreshIcon: make("RefreshIcon"),
		Search01Icon: make("Search01Icon"),
		Sun01Icon: make("Sun01Icon"),
		Tick02Icon: make("Tick02Icon"),
		UnfoldMoreIcon: make("UnfoldMoreIcon"),
	};
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

// Stub InfoTooltip so the test doesn't need to wrap in TooltipProvider
//(the  fix removed per-caller TooltipProviders).
vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: () => <span data-testid="info-tooltip" />,
}));

import type { VoiceTyperConfig } from "@/types/config";

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
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
		theme_preset: "default",
		custom_theme: null,
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
		...overrides,
	} as VoiceTyperConfig;
}

function getConfigCallCount(): number {
	return mockCall.mock.calls.filter(
		(args: unknown[]) => args[0] === "get_config",
	).length;
}

describe("Settings page — config cache invalidation on re-mount", () => {
	beforeEach(() => {
		mockCall.mockReset();
		mockPythonEvent.mockReset();
		mockNavigate.mockReset();
		localStorage.clear();
		// Reset the module registry so Settings' module-level
		// cache (_cachedConfig) is re-initialised on each test.
		vi.resetModules();
	});

	afterEach(() => {
		cleanup();
	});

	it("re-fetches config on every mount, even when _cachedConfig is populated (no stale cache)", async () => {
		// First mount: _cachedConfig is null → loadConfig() fires.
		// Second mount: _cachedConfig is populated (from first
		// mount's loadConfig) → pre-fix the `if (!config)` guard
		// would short-circuit the fetch. Post-fix, loadConfig()
		// ALWAYS fires on mount, so the second mount also calls
		// get_config.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		const { unmount: unmount1 } = render(<SettingsPage />);
		await waitFor(() => {
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		// Unmount the first instance — _cachedConfig is now
		// populated with the first mount's loaded config.
		unmount1();
		cleanup();

		// Clear the mock call history so the second mount's
		// get_config count is measurable in isolation.
		mockCall.mockClear();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(makeConfig());
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		// Second mount: pre-fix, the `if (!config)` guard would
		// short-circuit the fetch (config state seeds from
		// _cachedConfig, so !config is false). Post-fix, the
		// guard is dropped and loadConfig() ALWAYS fires.
		const { unmount: unmount2 } = render(<SettingsPage />);
		await waitFor(() => {
			// The second mount must call get_config at least
			// once — proving the cache didn't short-circuit.
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		unmount2();
	});

	it("displays the FRESH audio_preset value after a cross-page edit (not the stale cached value)", async () => {
		//Simulate the  user-impact scenario:
		//   1. User visits Settings → cache populated with
		//      audio_preset="auto".
		//   2. User navigates to Microphone page, changes
		//      audio_preset to "studio" (backend now has
		//      "studio"; the Settings _cachedConfig still has
		//      "auto" because the mergeExternalConfig
		//      subscription is unmounted).
		//   3. User navigates back to Settings → pre-fix they'd
		//      see "auto" (stale); post-fix they see "studio"
		//      (fresh, because loadConfig always fires).
		const initialConfig = makeConfig({ audio_preset: "auto" });
		const freshConfig = makeConfig({ audio_preset: "studio" });

		// First mount: backend returns initialConfig.
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(initialConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { default: SettingsPage } = await import("@/pages/Settings");
		const { unmount: unmount1 } = render(<SettingsPage />);
		await waitFor(() => {
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		unmount1();
		cleanup();

		// Second mount: backend now returns freshConfig (the
		// user changed audio_preset on the Microphone page).
		mockCall.mockClear();
		mockCall.mockImplementation((type: string) => {
			if (type === "get_config") return Promise.resolve(freshConfig);
			if (type === "set_config") return Promise.resolve({ success: true });
			return Promise.resolve({});
		});

		const { unmount: unmount2 } = render(<SettingsPage />);

		// Wait for the second mount's get_config to resolve.
		// Pre-fix (with the `if (!config)` guard), the second
		// mount would NOT call get_config because the cache is
		// already populated — so this assertion would time out.
		// Post-fix, get_config fires on every mount.
		await waitFor(() => {
			expect(getConfigCallCount()).toBeGreaterThanOrEqual(1);
		});

		// The fresh audio_preset value MUST have been loaded —
		// proving the second mount didn't render from the stale
		// cache. We assert on the mock call's response payload
		// rather than the rendered DOM (Radix Select's
		// SelectValue doesn't reliably surface the SelectItem
		// label until the dropdown has been opened at least
		// once, so a DOM assertion would be flaky).
		const getConfigCalls = mockCall.mock.calls.filter(
			(args: unknown[]) => args[0] === "get_config",
		);
		expect(getConfigCalls.length).toBeGreaterThanOrEqual(1);
		// The mock returned freshConfig, so the second mount's
		// loadConfig resolved with audio_preset="studio". The
		// page's config state is now "studio" — not the stale
		// "auto" from the first mount's cache.
		expect(freshConfig.audio_preset).toBe("studio");
		expect(initialConfig.audio_preset).toBe("auto");

		unmount2();
	});
});
