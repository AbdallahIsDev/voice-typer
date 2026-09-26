/**
 * Tests for `AudioSettingsSection` covering the "Test microphone"
 * cross-link row and the Microphone Quality enable-Switch.
 *
 * Background: the `audio_preset` config field is mutated from two
 * unrelated UI surfaces, (1) this Settings section's "Microphone
 * Quality" switch-only row + revealed "Quality preset" picker row (no
 * "off" option) + custom filter chain, and (2) the Microphone page's
 * `PresetAccordionSelector` (with its own test-record A/B workflow). The
 * two surfaces use different presentation patterns, and live on
 * different pages with no cross-link. Users who
 * discover the Audio Enhancement controls on the Microphone page may
 * not realise the same setting is also configurable under Settings →
 * Audio.
 *
 * The fix adds a "Test microphone" row at the bottom of the Audio
 * Enhancement card (with a "Go to Microphone" button) so the user knows
 * the duplicate surface exists. Combined with the cache-invalidation
 * fix (Settings always re-fetches on mount), edits made on either side
 * are visible on the other.
 *
 * The Microphone Quality row carries ONLY the enable-Switch: "off"
 * lives behind it, and enabling reveals the preset picker row below
 * (Auto / Studio / Noisy Room / Advanced). Turning the Switch off
 * stashes the current preset; turning it on restores it.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

// Radix Select's pointerDown handler calls
// `target.hasPointerCapture(pointerId)` which jsdom doesn't implement.
// Stub the methods Radix touches so opening the preset Select doesn't
// crash inside its event handlers.
if (
	typeof Element !== "undefined" &&
	typeof Element.prototype.hasPointerCapture !== "function"
) {
	Element.prototype.hasPointerCapture = function hasPointerCapture() {
		return false;
	};
	Element.prototype.setPointerCapture = function setPointerCapture() {};
	Element.prototype.releasePointerCapture = function releasePointerCapture() {};
}

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
import type { LausuConfig } from "@/types/config";

function makeConfig(overrides: Partial<LausuConfig> = {}): LausuConfig {
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
		vad_filter_enabled: true,
		...overrides,
	} as LausuConfig;
}

const alwaysVisible = () => true;

describe("AudioSettingsSection, 'Test microphone' cross-link row", () => {
	beforeEach(() => {
		resetStableMocks();
		vi.clearAllMocks();
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the cross-link row with the simplified text", () => {
		const { container } = render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The row label names the action; the info lives behind the
		// row's (mocked) info tooltip and mentions the Microphone page.
		expect(screen.getByText("Test microphone")).toBeTruthy();
		const tips = Array.from(
			container.querySelectorAll('[data-testid="info-tooltip"]'),
		).map((el) => el.getAttribute("data-text") ?? "");
		expect(tips.some((text) => /Microphone page/i.test(text))).toBe(true);
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
		// rendering the section, only an explicit click should
		// navigate. Pre-fix this would have caught an accidental
		// useEffect(() => navigate(...), []) regression.
		expect(mockNavigate).not.toHaveBeenCalled();
	});

	it("renders the cross-link as a row INSIDE the card (no banner)", () => {
		// The old banner was a sibling above the bordered card; the
		// merged row must live inside the card's divide-y container
		// with the other SettingRow rows.
		const { container } = render(
			<AudioSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// No banner anymore.
		expect(container.querySelector('[role="note"]')).toBeNull();
		// The button sits inside the card's row container.
		const button = screen.getByRole("button", {
			name: /Go to Microphone/i,
		});
		const card = container.querySelector(".divide-y");
		expect(card).toBeTruthy();
		expect(card?.contains(button)).toBe(true);
	});
});

describe("AudioSettingsSection, Microphone Quality enable-Switch", () => {
	beforeEach(() => {
		resetStableMocks();
		vi.clearAllMocks();
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	function renderSection(
		configOverrides: Partial<LausuConfig> = {},
		updateConfig = vi.fn(),
	) {
		const utils = render(
			<AudioSettingsSection
				config={makeConfig(configOverrides)}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		return { updateConfig, ...utils };
	}

	function qualitySwitch() {
		return screen.getByTestId("microphone-quality-switch");
	}

	it("renders Microphone Quality first, preset picker second", () => {
		const { container } = renderSection();
		const labels = Array.from(
			container.querySelectorAll("[data-settings-row-label]"),
		).map((el) => el.getAttribute("data-settings-row-label"));
		expect(labels.length).toBeGreaterThan(1);
		expect(labels[0]).toBe("Microphone Quality");
		expect(labels[1]).toBe("Quality preset");
	});

	it("switch is on for a real preset, off for 'off'", () => {
		renderSection({ audio_preset: "studio" });
		expect(qualitySwitch().getAttribute("aria-checked")).toBe("true");

		cleanup();
		renderSection({ audio_preset: "off" });
		expect(qualitySwitch().getAttribute("aria-checked")).toBe("false");
	});

	it("turning the switch off persists 'off'", () => {
		const { updateConfig } = renderSection({ audio_preset: "studio" });
		fireEvent.click(qualitySwitch());
		expect(updateConfig).toHaveBeenCalledWith({ audio_preset: "off" });
	});

	it("turning the switch on restores the previous preset (not 'auto')", () => {
		const { updateConfig, rerender } = renderSection({
			audio_preset: "studio",
		});
		// External change to "off" (e.g. from the Microphone page),
		// then flip the Switch back on.
		rerender(
			<AudioSettingsSection
				config={makeConfig({ audio_preset: "off" })}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		fireEvent.click(qualitySwitch());
		expect(updateConfig).toHaveBeenCalledWith({ audio_preset: "studio" });
	});

	it("preset Select offers no 'OFF' option (disabling is the Switch's job)", async () => {
		const user = userEvent.setup();
		renderSection({ audio_preset: "auto" });
		await user.click(screen.getByRole("combobox"));
		const options = screen.getAllByRole("option").map((o) => o.textContent);
		expect(options).toContain("Auto");
		expect(options).toContain("Studio");
		expect(options).toContain("Noisy Room");
		expect(options).toContain("Advanced");
		expect(options).not.toContain("OFF");
	});

	it("preset picker row is revealed only while the Switch is on", () => {
		const { rerender } = renderSection({ audio_preset: "auto" });
		expect(screen.queryByRole("combobox")).toBeTruthy();

		// Flip the preset off externally (e.g. from the Microphone
		// page): the picker row unmounts, the switch-only row stays.
		rerender(
			<AudioSettingsSection
				config={makeConfig({ audio_preset: "off" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		expect(screen.queryByRole("combobox")).toBeNull();
		expect(screen.getByTestId("microphone-quality-switch")).toBeTruthy();
	});

	it("picking a preset persists it", async () => {
		const user = userEvent.setup();
		const { updateConfig } = renderSection({ audio_preset: "auto" });
		await user.click(screen.getByRole("combobox"));
		await user.click(screen.getByRole("option", { name: "Studio" }));
		expect(updateConfig).toHaveBeenCalledWith({ audio_preset: "studio" });
	});
});

describe("AudioSettingsSection, voice activity filtering toggle", () => {
	beforeEach(() => {
		resetStableMocks();
		vi.clearAllMocks();
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	function renderSection(
		configOverrides: Partial<LausuConfig> = {},
		updateConfig = vi.fn(),
	) {
		render(
			<AudioSettingsSection
				config={makeConfig(configOverrides)}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		return updateConfig;
	}

	it("renders the toggle reflecting the persisted value", () => {
		renderSection({ vad_filter_enabled: true });
		const toggle = screen.getByTestId("vad-filter-switch");
		expect(toggle.getAttribute("aria-checked")).toBe("true");

		cleanup();
		renderSection({ vad_filter_enabled: false });
		expect(
			screen.getByTestId("vad-filter-switch").getAttribute("aria-checked"),
		).toBe("false");
	});

	it("persists the flipped value through set_config on click", () => {
		const updateConfig = renderSection({ vad_filter_enabled: true });
		fireEvent.click(screen.getByTestId("vad-filter-switch"));
		expect(updateConfig).toHaveBeenCalledWith({ vad_filter_enabled: false });
	});

	it("is searchable via the section search (label registered for filtering)", () => {
		// The row registers its label/info in sectionItems so settings
		// search can find it, assert the label renders (the row would
		// be absent if it were dropped from the visible surface).
		renderSection();
		expect(screen.getByText("Voice activity filtering")).toBeTruthy();
	});
});
