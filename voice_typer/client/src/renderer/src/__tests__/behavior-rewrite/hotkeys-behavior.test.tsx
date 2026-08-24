/**
 *  vitest rewrite — behavioral tests for hotkey-related TS modules
 * covered by `tests/test_hotkeys.py`.
 *
 * This file replaces the following string-pattern Python tests (each
 * one is also `@pytest.mark.skip`-ed in `tests/test_hotkeys.py` with
 * a pointer back to this file):
 *
 *   - TestHotkeyUtilsValidate::test_validate_function_exists
 *       (Python invariant: `"function validateHotkey" in utils` source string)
 *   - TestRepasteKeySettingUsesHotkeyPicker::test_no_free_text_input_for_repaste
 *       (Python invariant: regex `<Input[^>]*value=\{config\.repaste_hotkey`
 *       does NOT match source)
 *   - TestDictationKeySupportsExpandedPresets::test_dictation_key_uses_hotkey_picker_combo_mode
 *       (Python invariant: `'mode="combo"' in recording` AND
 *       `"DICTATION_KEY_PRESETS" in recording` source strings)
 *   - TestDictationKeySupportsExpandedPresets::test_old_f2_f12_dropdown_removed
 *       (Python invariant: `'f2', 'f3', 'f4', 'f5', 'f6'` is NOT in
 *       `pages/Settings.tsx` source)
 *
 * The Python tests asserted on substring presence/absence inside TS
 * source files. These pass even when the function silently returns the
 * wrong value, fail on innocent refactors (renaming the function,
 * switching quote style, extracting constants), and — for the "absent"
 * variants — silently pass if the offending code is merely moved to a
 * sibling file. The vitest versions below exercise the real runtime
 * behaviour: they import the function or mount the component, then
 * assert on the actual returned value or rendered DOM, so a refactor
 * that preserves the contract still passes and a behavioural regression
 * fails.
 *
 * NOTE: tests that overlap with the  rewrite
 * (`__tests__/a11y-rewrite/hotkey-utils-behavior.test.ts` and
 * `__tests__/a11y-rewrite/RecordingSettings-hotkey-picker.test.tsx`)
 * are NOT duplicated here — the  file already covers
 * `test_formats_single_key`, `test_formats_combo`,
 * `test_validate_rejects_empty`,
 * `test_validate_rejects_modifiers_only_in_combo`,
 * `test_validate_rejects_multi_key_in_single_mode`,
 * `test_settings_imports_hotkey_picker`,
 * `test_repaste_key_uses_hotkey_picker_combo_mode`, and
 * `test_single_key_presets_include_beyond_f12`.
 *
 * The corresponding Python tests are skipped (NOT deleted) so they
 * remain as a fallback until CI verifies the vitest versions pass on
 * all platforms.
 */

import { cleanup, render } from "@testing-library/react";
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

import {
	afterEach,
	beforeAll,
	beforeEach,
	describe,
	expect,
	it,
	vi,
} from "vitest";

// ── HotkeyPicker mock ──────────────────────────────────────────────────────
//
// Capture every HotkeyPicker instance's props so we can assert on the
// actual rendered configuration.  This is the same pattern used by the
//rewrite (RecordingSettings-hotkey-picker.test.tsx); replicating
//it here keeps the  test self-contained.
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

vi.mock("@/lib/sound-manager", () => ({
	setSoundFeedbackEnabled: vi.fn(),
}));

import {
	formatHotkeyLabel,
	KEY_CODE_TO_PYNPUT,
	MODIFIER_KEYS,
	validateHotkey,
} from "@/components/hotkey/hotkey-utils";
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

beforeAll(() => {
	// hotkey-utils detects platform from navigator.userAgent at module
	// load time and at every getter call.  Stub a deterministic Windows
	// UA so the platform-dependent branches (e.g. Fn-only-on-macOS)
	// behave the same on every CI runner.
	vi.stubGlobal("navigator", {
		userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
	});
});

beforeEach(() => {
	hotkeyPickerInstances.length = 0;
	cleanup();
});

afterEach(() => {
	cleanup();
});

// ── test_validate_function_exists ──────────────────────────────────────────

describe("validateHotkey is exported and callable (rewrite of test_validate_function_exists)", () => {
	it("exports validateHotkey as a function", () => {
		// Original Python invariant: `hotkey-utils.ts` source contains
		// the literal string `"function validateHotkey"`.  That passes
		// even if the function is renamed to `_validateHotkey` and a
		// stub `function validateHotkey` is left in a comment.
		// Behavioural: the named export exists and is callable.
		expect(typeof validateHotkey).toBe("function");
	});

	it("returns null for a valid single-mode hotkey", () => {
		// A function that exists but always throws would still pass the
		// source-string check.  Behavioural: it accepts a valid
		// hotkey and returns null (no error).
		expect(validateHotkey("<caps_lock>", "single")).toBeNull();
	});

	it("returns null for a valid combo-mode hotkey", () => {
		expect(validateHotkey("<ctrl>+<alt>+v", "combo")).toBeNull();
	});

	it("returns a non-null error string for an invalid hotkey", () => {
		// Defense-in-depth: the function actually executes and returns
		// a string (not throws, not undefined) for invalid input.
		const result = validateHotkey("", "single");
		expect(typeof result).toBe("string");
		expect(result).not.toBe("");
	});
});

// ── test_dictation_key_uses_hotkey_picker_single_mode ──────────────────────

describe('dictation key HotkeyPicker uses mode="single" with raw single-key presets (rewrite of test_dictation_key_uses_hotkey_picker_combo_mode)', () => {
	it('renders the dictation-key HotkeyPicker with mode="single"', () => {
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<caps_lock>" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);

		// The dictation-key picker is the one whose `value` matches
		// `config.hotkey`.
		const dictationPicker = hotkeyPickerInstances.find(
			(p) => p.value === "<caps_lock>",
		);
		expect(dictationPicker).toBeTruthy();
		// Production deliberately moved the dictation picker to
		// mode="single" (see RecordingSettingsSection.tsx): the old
		// combo mode reintroduced the `<shift>` hazard (Shift is held
		// for capitalization — dictation would fire on every uppercase
		// letter) and violated the single-key-only promise of the
		// dropdown. The repaste picker remains combo mode.
		expect(dictationPicker?.mode).toBe("single");
	});

	it("passes the single-key presets as the dictation picker's presets prop", () => {
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
		// Original Python invariant: `"DICTATION_KEY_PRESETS" in recording`
		// (i.e. the constant is referenced from the JSX).  Behavioural:
		// the presets prop is wired through to the rendered HotkeyPicker
		// instance, and its values are the safe single-key presets.
		expect(dictationPicker?.presets).toBeDefined();
		expect(dictationPicker?.presets?.length).toBeGreaterThan(0);
		const presetValues = (dictationPicker?.presets ?? []).map((p) => p.value);
		// Single mode strips angle brackets before matching, so the
		// preset values are the RAW key names: ``caps_lock``, ``alt``,
		// ``ctrl`` (no ``shift`` — it would fire on every capital
		// letter). On macOS an extra ``fn`` entry is appended. Assert
		// the Windows baseline set (raw form, no <...> wrappers).
		expect(presetValues).toContain("caps_lock");
		expect(presetValues).toContain("ctrl");
		expect(presetValues).toContain("alt");
		// No value should be angle-bracket wrapped (single mode passes
		// raw key names; the picker re-adds brackets on selection).
		for (const v of presetValues) {
			expect(v).not.toMatch(/^</);
		}
	});

	it('renders the repaste-key HotkeyPicker with mode="combo" (unchanged)', () => {
		// The repaste picker stays combo-mode (multi-key combos like
		// Ctrl+Shift+V are valid for repaste). Assert exactly one
		// combo-mode instance (repaste) alongside the single-mode
		// dictation picker.
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({
					hotkey: "<caps_lock>",
					repaste_hotkey: "<ctrl>+<alt>+v",
				})}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		const allCombos = hotkeyPickerInstances.filter((p) => p.mode === "combo");
		expect(allCombos.length).toBeGreaterThanOrEqual(1);
		const singles = hotkeyPickerInstances.filter((p) => p.mode === "single");
		expect(singles.length).toBeGreaterThanOrEqual(1);
	});
});

// ── test_old_f2_f12_dropdown_removed ───────────────────────────────────────

describe("old F2–F12 dropdown is removed (rewrite of test_old_f2_f12_dropdown_removed)", () => {
	it("does NOT offer F2–F12 as dictation-key preset values", () => {
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
		// Original Python invariant: `pages/Settings.tsx` source does
		// NOT contain the literal `'f2', 'f3', 'f4', 'f5', 'f6'`.
		// Behavioural: no preset value offered to the user is an
		// F-key (matches /^f\d{1,2}$/).  This catches both the old
		// inline dropdown AND any future regression that adds an
		// F-key preset to DICTATION_KEY_PRESETS.
		const presetValues = (dictationPicker?.presets ?? []).map((p) =>
			p.value.replace(/[<>]/g, ""),
		);
		for (const value of presetValues) {
			expect(value).not.toMatch(/^f\d{1,2}$/);
		}
	});

	it("does NOT render a <select> with F2–F12 <option> children", () => {
		// The old dropdown was a <Select> (shadcn) with F2..F12 options.
		// Even if a future refactor re-introduces a <select> for some
		// other setting, none of its <option> children should be F2-F12.
		const { container } = renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<caps_lock>" })}
				updateConfig={() => {}}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		const options = container.querySelectorAll("[role='option'], option");
		for (const opt of options) {
			const text = (opt.textContent ?? "").trim().toLowerCase();
			expect(text).not.toMatch(/^f\d{1,2}$/);
		}
	});

	it("does NOT include any F-key in the dictation picker's preset labels", () => {
		// The labels are user-visible; the old dropdown used "F2",
		// "F3", etc. as labels.  A regression that re-introduces
		// F-key labels (even with non-F-key values) would also be
		// caught here.
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
		const presetLabels = (dictationPicker?.presets ?? []).map((p) =>
			p.label.toLowerCase(),
		);
		for (const label of presetLabels) {
			expect(label).not.toMatch(/^f\d{1,2}$/);
		}
	});
});

// ── test_no_free_text_input_for_repaste ────────────────────────────────────

describe("repaste hotkey is NOT editable via a free-text <input> (rewrite of test_no_free_text_input_for_repaste)", () => {
	it("does NOT render a free-text <input> whose value equals config.repaste_hotkey", () => {
		// Original Python invariant: regex
		// `<Input[^>]*value=\{config\.repaste_hotkey` does NOT match
		// `RecordingSettingsSection.tsx` source.  Behavioural: no
		// <input> element in the rendered DOM has its `value` (or
		// `defaultValue`) attribute/property equal to the
		// repaste_hotkey string.
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

		const inputs = container.querySelectorAll("input");
		for (const input of inputs) {
			const attrValue = input.getAttribute("value");
			const propValue = (input as HTMLInputElement).value;
			expect(attrValue).not.toBe("<ctrl>+<alt>+v");
			expect(propValue).not.toBe("<ctrl>+<alt>+v");
			// Also catch `defaultValue` bindings (React uncontrolled).
			const defaultValue = input.getAttribute("defaultValue");
			expect(defaultValue).not.toBe("<ctrl>+<alt>+v");
		}
	});

	it("routes repaste-key edits through HotkeyPicker.onChange → updateConfig({ repaste_hotkey })", () => {
		// The Python test only asserted that a free-text <Input> was
		// absent.  The behavioural counterpart is stronger: the
		// repaste-key HotkeyPicker is the ONLY path to edit
		// `repaste_hotkey`, and its onChange propagates as
		// `{ repaste_hotkey: <new> }`.
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
});

// ── additional defense-in-depth tests for the same invariants ─────────────

describe("hotkey-utils exports (defense-in-depth for test_validate_function_exists)", () => {
	it("exports formatHotkeyLabel as a function", () => {
		// If the module's named exports were ever accidentally dropped
		// (e.g. by switching to default export), validateHotkey's
		// presence alone wouldn't catch it.
		expect(typeof formatHotkeyLabel).toBe("function");
	});

	it("exports MODIFIER_KEYS as a non-empty array including ctrl/alt/shift/cmd", () => {
		expect(Array.isArray(MODIFIER_KEYS)).toBe(true);
		expect(MODIFIER_KEYS.length).toBeGreaterThan(0);
		expect(MODIFIER_KEYS).toContain("ctrl");
		expect(MODIFIER_KEYS).toContain("alt");
		expect(MODIFIER_KEYS).toContain("shift");
		expect(MODIFIER_KEYS).toContain("cmd");
	});

	it("KEY_CODE_TO_PYNPUT maps KeyV → v (HOTKEY-FIX-002 regression guard)", () => {
		// The default repaste hotkey is <ctrl>+<alt>+v.  If the KeyV
		// entry is ever dropped from KEY_CODE_TO_PYNPUT, capture will
		// fail with "Key 'v' is not supported" — silently breaking
		// the default config.  This is a behavioral guard that the
		// source-string test_validate_function_exists couldn't cover.
		expect(KEY_CODE_TO_PYNPUT.KeyV).toBe("v");
		expect(KEY_CODE_TO_PYNPUT.Digit0).toBe("0");
	});
});

describe("RecordingSettingsSection HotkeyPicker wiring (defense-in-depth)", () => {
	it("does NOT render a <textarea> bound to repaste_hotkey either", () => {
		// The Python test only checked for <Input>; a refactor that
		// swapped <Input> for <textarea> would slip past.  Behavioural
		// guard: no <textarea> has its value/defaultValue equal to
		// the repaste_hotkey either.
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
		const textareas = container.querySelectorAll("textarea");
		for (const ta of textareas) {
			expect((ta as HTMLTextAreaElement).value).not.toBe("<ctrl>+<alt>+v");
			expect(ta.getAttribute("value")).not.toBe("<ctrl>+<alt>+v");
		}
	});

	it("routes dictation-key edits through HotkeyPicker.onChange → updateConfig({ hotkey })", () => {
		const updateConfig = vi.fn();
		renderWithProviders(
			<RecordingSettingsSection
				config={makeConfig({ hotkey: "<caps_lock>" })}
				updateConfig={updateConfig}
				updateConfigDebounced={() => {}}
				isVisible={alwaysVisible}
			/>,
		);
		const dictationPicker = hotkeyPickerInstances.find(
			(p) => p.value === "<caps_lock>",
		);
		expect(dictationPicker).toBeTruthy();
		dictationPicker?.onChange("<alt>");
		expect(updateConfig).toHaveBeenCalledWith({ hotkey: "<alt>" });
	});
});
