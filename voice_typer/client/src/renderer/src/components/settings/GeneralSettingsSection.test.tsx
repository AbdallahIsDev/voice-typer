/**
 * Tests for GeneralSettingsSection — locale re-rendering (B-REVIEW-3).
 *
 * b-review Finding 3 documented that the 10 *_LABEL / *_INFO
 * translation constants used to live at module scope, so they were
 * FROZEN to whatever locale was active on first import. Switching the
 * locale at runtime therefore left the General section showing the
 * OLD locale's labels until a full ``window.location.reload()``
 * re-imported the module.
 *
 * The fix moved the constants INSIDE the component body and the
 * section subscribes to locale changes via the ``useT()`` hook
 * (useSyncExternalStore over i18n's ``subscribeLocale``). ``setLocale``
 * notifies subscribers, so the section re-renders with the CURRENT
 * locale WITHOUT a full page reload. These tests verify that:
 *   1. The section renders English labels when the locale is "en".
 *   2. After ``setLocale("ar")`` the labels switch to Arabic — WITHOUT
 *      ``window.location.reload()`` (the reload was removed entirely).
 *   3. The round-trip ar -> en also re-renders in-place.
 */
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) => {
	const wrapped = (node: React.ReactElement) => (
		<TooltipProvider delayDuration={200}>{node}</TooltipProvider>
	);
	const utils = render(wrapped(ui));
	return {
		...utils,
		rerender: (node: React.ReactElement) => utils.rerender(wrapped(node)),
	};
};

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Stub the hugeicons runtime so we don't pull in the real SVG renderer
// (heavy + depends on browser-only APIs that jsdom doesn't implement).
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

import { GeneralSettingsSection } from "@/components/settings/GeneralSettingsSection";
import type { SettingsSectionSharedProps } from "@/components/settings/types";
import { type Locale, setLocale } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

/** Minimal config that satisfies GeneralSettingsSection's render path. */
function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		fast_startup: true,
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
		history_retention_days: 30,
		history_retention_count: 100,
		history_max_entries: 1000,
		onboarding_completed: true,
		tray_left_click_action: "open_app",
		theme_mode: "system",
		theme_preset: "default",
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
		...overrides,
	} as VoiceTyperConfig;
}

/** isVisible that always returns true (we're not testing search here). */
const alwaysVisible: SettingsSectionSharedProps["isVisible"] = () => true;

describe("GeneralSettingsSection — B-REVIEW-3 locale re-rendering", () => {
	const originalReload = window.location.reload;

	beforeEach(() => {
		// Spy on window.location.reload so we can assert it's NOT
		// called during the in-test locale switch — the whole
		// point of B-REVIEW-3 is that the section re-renders
		// WITHOUT a reload.
		// jsdom's window.location.reload is a no-op stub; replace
		// it with a spy so we can assert call count.
		Object.defineProperty(window, "location", {
			configurable: true,
			writable: true,
			value: {
				...window.location,
				reload: vi.fn(),
			},
		});
		// Start each test in English.
		act(() => {
			setLocale("en" as Locale);
		});
	});

	afterEach(() => {
		// Restore the original location object.
		Object.defineProperty(window, "location", {
			configurable: true,
			writable: true,
			value: {
				...window.location,
				reload: originalReload,
			},
		});
		act(() => {
			setLocale("en" as Locale);
		});
		cleanup();
	});

	it("renders English labels when locale is 'en'", () => {
		renderWithProviders(
			<GeneralSettingsSection
				config={makeConfig()}
				updateConfig={vi.fn()}
				updateConfigDebounced={vi.fn()}
				isVisible={alwaysVisible}
			/>,
		);
		// "Launch at Login" is the LAUNCH_AT_LOGIN_LABEL constant.
		expect(screen.getByText("Launch at Login")).toBeTruthy();
		// "Notifications" is the NOTIFICATIONS_LABEL constant.
		expect(screen.getByText("Notifications")).toBeTruthy();
	});

	it("re-renders Arabic labels after setLocale('ar') WITHOUT window.location.reload()", async () => {
		const { rerender } = renderWithProviders(
			<GeneralSettingsSection
				config={makeConfig()}
				updateConfig={vi.fn()}
				updateConfigDebounced={vi.fn()}
				isVisible={alwaysVisible}
			/>,
		);

		// Sanity check: English label present on first render.
		expect(screen.getByText("Launch at Login")).toBeTruthy();

		// Switch the locale to Arabic — this is the action that
		// USED TO require a full page reload to take effect on
		// this section.
		act(() => {
			setLocale("ar" as Locale);
		});

		// Re-render the SAME component instance. No reload.
		rerender(
			<GeneralSettingsSection
				config={makeConfig()}
				updateConfig={vi.fn()}
				updateConfigDebounced={vi.fn()}
				isVisible={alwaysVisible}
			/>,
		);

		// The Arabic translation table loads via an async dynamic import
		// (ensureLocaleLoaded), so the label swap lands on the second
		// subscriber notification. Wait for it — the load completes in a
		// microtask chain, well within waitFor's budget.
		await waitFor(() => {
			// The English label must be GONE (replaced by Arabic).
			expect(screen.queryByText("Launch at Login")).toBeNull();
			// The Arabic translation of "Launch at Login" is
			// "التشغيل عند تسجيل الدخول" (verified in ar.json).
			expect(screen.getByText("التشغيل عند تسجيل الدخول")).toBeTruthy();
		});

		// CRITICAL: the locale switch must NOT have triggered a
		// full page reload — the whole point of B-REVIEW-3 is
		// that the section re-renders in-place.
		expect(window.location.reload).not.toHaveBeenCalled();
	});

	it("re-renders English labels when switching ar -> en (round-trip)", async () => {
		// Start in Arabic.
		act(() => {
			setLocale("ar" as Locale);
		});

		const { rerender } = renderWithProviders(
			<GeneralSettingsSection
				config={makeConfig()}
				updateConfig={vi.fn()}
				updateConfigDebounced={vi.fn()}
				isVisible={alwaysVisible}
			/>,
		);

		// The Arabic table loads async — wait for the label to appear
		// before asserting the round-trip back to English.
		await waitFor(() => {
			expect(screen.getByText("التشغيل عند تسجيل الدخول")).toBeTruthy();
		});

		// Switch back to English.
		act(() => {
			setLocale("en" as Locale);
		});
		rerender(
			<GeneralSettingsSection
				config={makeConfig()}
				updateConfig={vi.fn()}
				updateConfigDebounced={vi.fn()}
				isVisible={alwaysVisible}
			/>,
		);

		expect(screen.getByText("Launch at Login")).toBeTruthy();
		expect(screen.queryByText("التشغيل عند تسجيل الدخول")).toBeNull();
	});
});
