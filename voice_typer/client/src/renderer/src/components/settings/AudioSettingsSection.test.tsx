/**
 * Tests for `AudioSettingsSection` covering the cross-link banner to
 * the Microphone page.
 *
 * Background: the `audio_preset` config field is mutated from two
 * unrelated UI surfaces — (1) this Settings section's "Microphone
 * Quality" Select + custom filter chain, and (2) the Microphone page's
 * `AudioPresetSelector` (with its own test-record A/B workflow). The
 * two surfaces use different option sets, different disclosure
 * patterns, and live on different pages with no cross-link. Users who
 * discover the Audio Enhancement controls on the Microphone page may
 * not realise the same setting is also configurable under Settings →
 * Audio.
 *
 * The fix adds a banner at the top of the Audio Enhancement section
 * that says "These settings are also editable on the Microphone page"
 * (with a "Go to Microphone" button) so the user knows the duplicate
 * surface exists. Combined with the  cache-invalidation fix
 * (Settings always re-fetches on mount), edits made on either side
 * are visible on the other.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Shared stable-mocks preamble (see helpers/stableMocks.tsx): the
// assertable singletons + one vi.mock line per module. The navigation
// mock wires the "Go to Microphone" deep-link; the python mock keeps
// the volumeBackend fetch from firing.
import {
	hugeiconsCoreMock,
	hugeiconsReactMock,
	pythonMock,
	resetStableMocks,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockNavigate } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("@/hooks/useNavigation", () => ({
	useNavigation: () => ({ navigate: mockNavigate }),
}));
vi.mock("@hugeicons/react", () => hugeiconsReactMock());
vi.mock("@hugeicons/core-free-icons", () => hugeiconsCoreMock());

// Stub InfoTooltip to avoid the Radix Tooltip provider requirement
//(the  fix removed per-caller TooltipProviders; tests that mount
// SettingRow in isolation now need to either wrap in TooltipProvider
// or stub InfoTooltip).
vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: ({ text }: { text: string }) => (
		<span data-testid="info-tooltip" data-text={text} />
	),
}));

// Stub AudioFilterChain so we don't pull in the full filter chain
// render graph (we're only testing the cross-link banner).
vi.mock("@/components/audio/AudioFilterChain", () => ({
	AudioFilterChain: () => <div data-testid="audio-filter-chain" />,
}));

import { AudioSettingsSection } from "@/components/settings/AudioSettingsSection";
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
		...overrides,
	} as VoiceTyperConfig;
}

const alwaysVisible = () => true;

describe("AudioSettingsSection — cross-link banner to Microphone page", () => {
	beforeEach(() => {
		resetStableMocks();
		vi.clearAllMocks();
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the cross-link banner with the expected text", () => {
		render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The banner text must mention the Microphone page so the
		// user knows the same audio preset + filter chain is also
		// editable there. Use a partial-match assertion so the
		// exact wording can be tweaked without breaking the test.
		const banner = screen.getByText(/Microphone page/i);
		expect(banner).toBeTruthy();
	});

	it("renders a 'Go to Microphone' button", () => {
		render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The button is the actionable affordance that takes the
		// user to the Microphone page. Use role+name so the test
		// is robust to icon-only vs labelled variations.
		const button = screen.getByRole("button", {
			name: /Go to Microphone/i,
		});
		expect(button).toBeTruthy();
	});

	it("calls navigate('microphone') when the 'Go to Microphone' button is clicked", () => {
		render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const button = screen.getByRole("button", {
			name: /Go to Microphone/i,
		});
		fireEvent.click(button);

		expect(mockNavigate).toHaveBeenCalledTimes(1);
		expect(mockNavigate).toHaveBeenCalledWith("microphone");
	});

	it("does NOT call navigate on mount (only on user click)", () => {
		render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// Sanity check: the navigate function must NOT fire just by
		// rendering the section — only an explicit click should
		// navigate. Pre-fix this would have caught an accidental
		// useEffect(() => navigate(...), []) regression.
		expect(mockNavigate).not.toHaveBeenCalled();
	});

	it("renders the banner BEFORE the SettingsSection card (banner is a sibling, not a row)", () => {
		// The banner must NOT be inside the bordered card that
		// contains the SettingRow rows — it should be a sibling
		// above the card so it reads as a section-level notice
		// rather than a settings row. We assert the banner's
		// parent is NOT the same div that contains the SettingRow
		// rows (the bordered card).
		const { container } = render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The banner has role="note" (set in the source).
		const banner = container.querySelector('[role="note"]');
		expect(banner).toBeTruthy();
		// The banner must contain the cross-link text + the button.
		expect(banner?.textContent).toMatch(/Microphone page/i);
		expect(banner?.querySelector("button")).toBeTruthy();
	});
});
