/**
 *  vitest rewrite — behavioral tests for `RecordingSettingsSection.tsx`
 * HotkeyPicker wiring.
 *
 * Replaces the following string-pattern Python tests from
 * `tests/test_hotkeys.py`:
 *   - TestRepasteKeySettingUsesHotkeyPicker::test_settings_imports_hotkey_picker
 *   - TestRepasteKeySettingUsesHotkeyPicker::test_repaste_key_uses_hotkey_picker_combo_mode
 *
 * The Python tests asserted on substring presence inside
 * `RecordingSettingsSection.tsx`:
 *   - `"import { HotkeyPicker }" in recording`
 *   - `"@/components/hotkey/HotkeyPicker" in recording`
 *   - `"<HotkeyPicker" in recording`
 *   - `'mode="combo"' in recording`
 *   - `"repaste_hotkey" in recording`
 *
 * These pass even when the HotkeyPicker is imported but never
 * rendered, when `mode="combo"` appears in a comment but not on the
 * actual JSX, or when the picker is rendered for the dictation key
 * but silently swapped to a free-text Input for the repaste key.
 * The vitest version below mocks HotkeyPicker so the test can
 * observe every instance rendered with its real props, then asserts:
 *   1. HotkeyPicker is rendered at least twice (dictation + repaste).
 *   2. The repaste-key instance has mode="combo"; the dictation-key
 *      instance has mode="single" (single-key-only, matching the
 *      single-key dropdown presets).
 *   3. The repaste-key instance's `value` is bound to the config's
 *      `repaste_hotkey` field and its `onChange` propagates back as
 *      `{ repaste_hotkey: <new value> }`.
 *   4. No free-text `<Input>` is rendered with `value={config.repaste_hotkey}`
 *      — i.e. the only way the user can edit the repaste key is via
 *      the HotkeyPicker.
 *
 * The corresponding Python tests are skipped via `@pytest.mark.skip`
 * with a pointer back to this file.  They are NOT deleted.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { TooltipProvider } from "@/components/ui/tooltip";

const renderWithProviders = (ui: React.ReactElement) =>
	render(<TooltipProvider delayDuration={200}>{ui}</TooltipProvider>);

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Capture every HotkeyPicker instance's props so we can assert on the
// actual rendered configuration.
const hotkeyPickerInstances: Array<{
	value: string;
	mode: "single" | "combo";
	ariaLabel?: string;
	onChange: (h: string) => void;
}> = [];

vi.mock("@/components/hotkey/HotkeyPicker", () => ({
	HotkeyPicker: (props: {
		value: string;
		mode: "single" | "combo";
		"aria-label"?: string;
		onChange: (h: string) => void;
	}) => {
		// Push a snapshot of props on every render.
		hotkeyPickerInstances.push({
			value: props.value,
			mode: props.mode,
			ariaLabel: props["aria-label"],
			onChange: props.onChange,
		});
		return (
			<div
				data-testid="hotkey-picker"
				data-mode={props.mode}
				data-aria-label={props["aria-label"] ?? ""}
				data-value={props.value}
			/>
		);
	},
}));

vi.mock("@hugeicons/react", () => ({
	HugeiconsIcon: () => <span data-testid="hugeicon" />,
}));

vi.mock("@hugeicons/core-free-icons", async () => {
	const { createHugeiconsMock } = await import(
		"@/__tests__/helpers/hugeicons-mock"
	);
	return createHugeiconsMock();
});

// Stub sound-manager so RecordingSettingsSection's toggle handler
// doesn't touch localStorage or play sounds during the test.
vi.mock("@/lib/sound-manager", () => ({
	setSoundFeedbackEnabled: vi.fn(),
}));

import { RecordingSettingsSection } from "@/components/settings/RecordingSettingsSection";
import type { VoiceTyperConfig } from "@/types/config";

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		fast_startup: true,
		hotkey: "<caps_lock>",
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
		repaste_hotkey: "<ctrl>+<alt>+v",
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
		...overrides,
	} as VoiceTyperConfig;
}

const alwaysVisible = () => true;

describe("RecordingSettings HotkeyPicker — RW-0 rewrite of test_settings_imports_hotkey_picker", () => {
	beforeEach(() => {
		hotkeyPickerInstances.length = 0;
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders the HotkeyPicker component (import succeeds and the JSX element appears in the tree)", () => {
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The Python invariant: RecordingSettingsSection.tsx
		// source contains `"import { HotkeyPicker }"` and
		// `"<HotkeyPicker"`.  Behavioral: the rendered DOM
		// contains at least one HotkeyPicker instance (the
		// mock surfaces it via data-testid).
		const pickers = screen.getAllByTestId("hotkey-picker");
		expect(pickers.length).toBeGreaterThanOrEqual(1);
	});
});

describe("RecordingSettings repaste HotkeyPicker — RW-0 rewrite of test_repaste_key_uses_hotkey_picker_combo_mode", () => {
	beforeEach(() => {
		hotkeyPickerInstances.length = 0;
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it("renders HotkeyPicker for both the dictation key and the repaste key", () => {
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig()}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The section renders HotkeyPicker for BOTH the
		// dictation key (config.hotkey) and the repaste key
		// (config.repaste_hotkey).  We expect at least 2
		// instances; the mock captures every render.
		expect(hotkeyPickerInstances.length).toBeGreaterThanOrEqual(2);
	});

	it('renders the repaste HotkeyPicker with mode="combo"', () => {
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({
					repaste_hotkey: "<ctrl>+<shift>+v",
				})}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// Find the HotkeyPicker instance whose `value`
		// matches the repaste_hotkey from config.
		const repastePicker = hotkeyPickerInstances.find(
			(p) => p.value === "<ctrl>+<shift>+v",
		);
		expect(repastePicker).toBeTruthy();
		// The Python invariant: `'mode="combo"' in recording`
		// AND `"repaste_hotkey" in recording`.  Behavioral:
		// the HotkeyPicker rendered for the repaste key has
		// mode="combo".
		expect(repastePicker?.mode).toBe("combo");
	});

	it('renders the dictation-key HotkeyPicker with mode="single" (matches the single-key dropdown presets)', () => {
		// The dictation key is rendered via HotkeyPicker in SINGLE mode
		// so the capture validator rejects multi-key combos. This
		// matches the dropdown's promise: getSingleKeyPresets() returns
		// only single-key options (caps_lock, alt, ctrl, fn-on-macOS),
		// so a user who picks from the dropdown gets single-key
		// behaviour. Pre-fix, the dictation-key picker was wired with
		// mode="combo" while the dropdown was filtered to single keys —
		// the capture silently accepted multi-key combos with no UI
		// hint, breaking the dropdown's promise.
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<caps_lock>" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const dictationPicker = hotkeyPickerInstances.find(
			(p) => p.value === "<caps_lock>",
		);
		expect(dictationPicker).toBeTruthy();
		expect(dictationPicker?.mode).toBe("single");
	});

	it("propagates repaste HotkeyPicker onChange to updateConfig({ repaste_hotkey })", () => {
		const updateConfig = vi.fn();
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({
					repaste_hotkey: "<ctrl>+<alt>+v",
				})}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const repastePicker = hotkeyPickerInstances.find(
			(p) => p.value === "<ctrl>+<alt>+v",
		);
		expect(repastePicker).toBeTruthy();
		repastePicker?.onChange("<cmd>+<shift>+v");
		expect(updateConfig).toHaveBeenCalledWith({
			repaste_hotkey: "<cmd>+<shift>+v",
		});
	});

	it("does NOT render a free-text <input> bound to config.repaste_hotkey", () => {
		const { container } = renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({
					repaste_hotkey: "<ctrl>+<alt>+v",
				})}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The Python invariant (test_no_free_text_input_for_repaste):
		// the source must NOT contain a regex matching
		// `<Input ... value={config.repaste_hotkey ...`.
		// Behavioral: no <input> element in the rendered DOM
		// has a value attribute equal to the repaste_hotkey
		// string ("<ctrl>+<alt>+v").  (The HotkeyPicker mock
		// doesn't render a real <input> — it renders a div
		// with data-testid="hotkey-picker".)
		const inputs = container.querySelectorAll("input");
		for (const input of inputs) {
			expect(input.getAttribute("value")).not.toBe("<ctrl>+<alt>+v");
			expect((input as HTMLInputElement).value).not.toBe("<ctrl>+<alt>+v");
		}
	});
});
