/**
 *  — shared test fixtures for the renderer vitest suite.
 *
 * Before this file existed, ~6 test files each declared their own
 * `baseConfig: VoiceTyperConfig = { ... 100+ fields ... }` constant
 * (see `__tests__/behavior-rewrite/feature-hardening-behavior.test.tsx`,
 * `pages/__tests__/Settings.test.tsx`, etc.). When a new config field
 * was added upstream, every copy went stale until a test failed for an
 * unrelated reason. `makeConfig(overrides)` collapses all those copies
 * into a single source of truth and lets each test override only the
 * fields it cares about:
 *
 *   const cfg = makeConfig({ bubble_position: "bottom" });
 *
 * The default values mirror the Python `Config` dataclass defaults
 * (see `voice_typer/server/config.py`) so tests don't accidentally
 * depend on a fixture that drifted from production.
 *
 * This file is intended to be imported directly by tests — it has NO
 * side effects on import (no `vi.mock`, no `window.*` mutation). Those
 * concerns live in `mocks.ts` and the per-test setup hooks.
 */
import type { VoiceTyperConfig } from "@/types/config";

/**
 * The canonical default `VoiceTyperConfig` used by every renderer test.
 *
 * Parity contract:
 *   - `schema_version` and `llm_preset` MUST mirror the Python `Config`
 *     dataclass defaults in `voice_typer/server/config.py` /
 *     `voice_typer/server/config_internals/migrations.py`. These two
 *     fields are pinned by `__tests__/helpers/__tests__/fixtures.test.ts`
 *     — a future contributor who lets either drift will see a loud
 *     vitest failure.
 *   - The remaining fields use TEST-DETERMINISM OVERRIDES (NOT Python
 *     defaults) so tests don't flake on platform-dependent or
 *     environment-sensitive behavior. Examples:
 *       `device: "cpu"` (Python: `"cuda"`), `autostart: false`
 *       (Python: `true`), `fast_startup: false` (Python: `true`),
 *       `waveform_bubble: true` (Python: `false`),
 *       `volume_duck_enabled: false` (Python: `true`),
 *       `noise_filter_enabled: false` (Python: `true`),
 *       `streaming_*: 0/false` (Python: real values),
 *       `history_retention_days: 30` (Python: `90`),
 *       `onboarding_completed: true` (Python: `false`).
 *     The full intentional-drift set is documented in
 *     `fixtures.test.ts`'s top comment; do NOT "fix" these overrides
 *     back to the Python defaults without auditing every test that
 *     relies on the override.
 *   - If a default changes upstream in a way that is NOT a
 *     test-determinism override, fix it here ONCE rather than across
 *     N test files.
 */
export const DEFAULT_CONFIG: VoiceTyperConfig = {
	schema_version: 3,
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
	fast_startup: false,

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
	llm_preset: "professional",

	crash_recovery_enabled: true,

	audio_quality_warnings: false,

	waveform_bubble: true,
	bubble_position: "top",
	bubble_behavior: "show_on_record",
	bubble_draggable: true,
	bubble_show_on_startup: false,
	bubble_click_to_toggle: true,
	bubble_mic_button: true,

	//persisted bubble position. Default `null` so the bubble
	// falls back to the platform-default position computed from
	// `bubble_position` ("top" / "bottom"). Tests that need to assert
	// on restore-position behaviour override both axes together via
	// `makeConfig({ bubble_x: 100, bubble_y: 200 })`.
	bubble_x: null,
	bubble_y: null,

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
};

/**
 * Build a complete `VoiceTyperConfig` for tests, overriding only the
 * fields the test cares about.
 *
 *   const cfg = makeConfig({ bubble_position: "bottom", hotkey: "F4" });
 *
 * Overrides are applied via shallow merge — provide the full value for
 * nested fields (e.g. `custom_theme` must be the entire `{light, dark}`
 * object, not a partial). The default `custom_theme` is `null` (matches
 * the Python dataclass default); tests that need a populated custom
 * theme should pass the whole object via the override.
 */
export function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return { ...DEFAULT_CONFIG, ...overrides };
}
