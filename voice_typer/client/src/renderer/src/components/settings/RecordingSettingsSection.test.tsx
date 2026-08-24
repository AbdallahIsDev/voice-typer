/**
 * Tests for `RecordingSettingsSection` covering the dictation-key
 * `HotkeyPicker` `mode="single"` fix and the repaste-key presets
 * memoization.
 *
 * Background: the dictation-key picker was previously wired with
 * `mode="combo"` while its dropdown presets came from
 * `getSingleKeyPresets()` (single-key only). In `mode="combo"` the
 * capture validator silently accepted multi-key combos like
 * `<ctrl>+<shift>`, breaking the dropdown's "single keys only" promise.
 * The fix changes the dictation-key picker to `mode="single"` so the
 * capture validator rejects multi-key combos (matching the dropdown).
 *
 * Separately, the repaste-key presets were derived inline via
 * `getComboPresets()` on every render. `getComboPresets()` re-detects
 * the platform on every call (so the macOS Cmd+Shift+V option appears
 * iff the current navigator.userAgent looks like macOS) but it returns
 * a fresh array reference each time, thrashing the `HotkeyPicker`
 * presets prop comparison. The fix wraps `getComboPresets()` in
 * `useMemo` so the array identity stays stable across renders.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Capture every HotkeyPicker instance's props so we can assert on the
// actual rendered configuration (mode, presets, value).
const hotkeyPickerInstances: Array<{
	value: string;
	mode: "single" | "combo";
	presets?: { value: string; label: string }[];
	ariaLabel?: string;
	onChange: (h: string) => void;
}> = [];

vi.mock("@/components/hotkey/HotkeyPicker", () => ({
	HotkeyPicker: (props: {
		value: string;
		mode: "single" | "combo";
		presets?: { value: string; label: string }[];
		"aria-label"?: string;
		onChange: (h: string) => void;
	}) => {
		// Push a snapshot of props on every render so we can
		// assert on the rendered configuration.
		hotkeyPickerInstances.push({
			value: props.value,
			mode: props.mode,
			presets: props.presets,
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

// Stub InfoTooltip so the test doesn't need to wrap in TooltipProvider.
// The Radix Tooltip primitive (used inside InfoTooltip) throws
// "Tooltip must be used within TooltipProvider" when rendered without
// an ancestor provider. The App root mounts a single TooltipProvider
//(per the  fix that removed per-caller providers), but tests
// that mount SettingRow in isolation need to either wrap in
// TooltipProvider themselves or stub InfoTooltip. Stubbing is simpler
// and keeps the test focused on the HotkeyPicker wiring (not on
// tooltip rendering).
vi.mock("@/components/feedback/InfoTooltip", () => ({
	InfoTooltip: ({
		text,
		contextLabel,
	}: {
		text: string;
		contextLabel?: string;
	}) => (
		<span
			data-testid="info-tooltip"
			data-text={text}
			data-context-label={contextLabel ?? ""}
		/>
	),
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
		noise_filter_limiter_release_ms: 0,
		noise_filter_notch: false,
		noise_filter_notch_frequency_hz: 0,
		...overrides,
	} as VoiceTyperConfig;
}

const alwaysVisible = () => true;

describe("RecordingSettingsSection — dictation-key mode and repaste-key presets memoization", () => {
	beforeEach(() => {
		hotkeyPickerInstances.length = 0;
		cleanup();
	});

	afterEach(() => {
		cleanup();
	});

	it('renders the dictation-key HotkeyPicker with mode="single" (matches the single-key dropdown presets)', () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<caps_lock>" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// Find the HotkeyPicker instance whose `value` matches
		// the dictation-key config value.
		const dictationPicker = hotkeyPickerInstances.find(
			(p) => p.value === "<caps_lock>",
		);
		expect(dictationPicker).toBeTruthy();
		// Pre-fix this was "combo" — the capture validator
		// silently accepted multi-key combos, breaking the
		// single-key-only dropdown's promise.
		expect(dictationPicker?.mode).toBe("single");
	});

	it('renders the repaste-key HotkeyPicker with mode="combo" (unchanged)', () => {
		render(
			<RecordingSettingsSection
				config={makeConfig({
					repaste_hotkey: "<ctrl>+<shift>+v",
				})}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const repastePicker = hotkeyPickerInstances.find(
			(p) => p.value === "<ctrl>+<shift>+v",
		);
		expect(repastePicker).toBeTruthy();
		expect(repastePicker?.mode).toBe("combo");
	});

	it("passes the same repaste-key presets array reference across re-renders (memoized)", () => {
		// Pre-fix, `getComboPresets()` was called inline on every
		// render, returning a fresh array reference each time and
		// thrashing the HotkeyPicker's presets prop comparison.
		// Post-fix, the presets are wrapped in useMemo so the
		// array identity stays stable across renders.
		const { rerender } = render(
			<RecordingSettingsSection
				config={makeConfig({
					repaste_hotkey: "<ctrl>+<alt>+v",
				})}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// First render captures the repaste-key presets.
		const firstRepastePicker = hotkeyPickerInstances.find(
			(p) => p.value === "<ctrl>+<alt>+v",
		);
		expect(firstRepastePicker?.presets).toBeTruthy();
		const firstPresets = firstRepastePicker?.presets;

		// Re-render with a prop change (different repaste_hotkey
		// value) to force a re-render of the section. The
		// presets array identity should remain stable.
		hotkeyPickerInstances.length = 0;
		rerender(
			<RecordingSettingsSection
				config={makeConfig({
					repaste_hotkey: "<ctrl>+<shift>+v",
				})}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const secondRepastePicker = hotkeyPickerInstances.find(
			(p) => p.value === "<ctrl>+<shift>+v",
		);
		expect(secondRepastePicker?.presets).toBeTruthy();
		// The presets array reference MUST be the same across
		// re-renders — this is the memoization guarantee.
		expect(secondRepastePicker?.presets).toBe(firstPresets);
	});

	it("passes the same dictation-key presets array reference across re-renders (memoized)", () => {
		// The dictation-key presets are also memoized via the
		// useDictationKeyPresets hook (unchanged by this fix,
		// but worth pinning since the parallel repaste-key fix
		// established the pattern).
		const { rerender } = render(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<caps_lock>" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const firstDictationPicker = hotkeyPickerInstances.find(
			(p) => p.value === "<caps_lock>",
		);
		expect(firstDictationPicker?.presets).toBeTruthy();
		const firstPresets = firstDictationPicker?.presets;

		hotkeyPickerInstances.length = 0;
		rerender(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<ctrl>" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		const secondDictationPicker = hotkeyPickerInstances.find(
			(p) => p.value === "<ctrl>",
		);
		expect(secondDictationPicker?.presets).toBeTruthy();
		expect(secondDictationPicker?.presets).toBe(firstPresets);
	});

	it("dictation-key presets are NOT wrapped in angle brackets (single mode strips brackets before matching)", () => {
		// Pre-fix, the presets were wrapped in `<...>` to match
		// the combo-mode hotkey format. Post-fix (mode="single"),
		// the HotkeyPicker strips angle brackets from the stored
		// value before matching it against the preset list, so
		// the preset values must be the RAW key names.
		render(
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
		expect(dictationPicker?.presets).toBeTruthy();
		const presetValues = dictationPicker?.presets?.map((p) => p.value);
		// Every preset value must be the RAW key name (no `<...>`
		// wrapping). E.g. "caps_lock", NOT "<caps_lock>".
		for (const v of presetValues ?? []) {
			expect(v.startsWith("<")).toBe(false);
			expect(v.endsWith(">")).toBe(false);
		}
		// Sanity check: caps_lock should be in the preset list.
		expect(presetValues).toContain("caps_lock");
	});
});
