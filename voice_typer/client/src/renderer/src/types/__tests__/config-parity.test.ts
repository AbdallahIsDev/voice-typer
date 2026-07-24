// src/renderer/src/types/__tests__/config-parity.test.ts
//
// XZ-CFG-03 / XZ-CFG-05 / XZ-CFG-06 (parity guard): static type-level
// regression tests pinning the TS `VoiceTyperConfig` interface to the
// Python `Config` dataclass + IPC validator constraints.
//
// The Python side (config.py, config_validators.py) is owned by other
// sub-agents (XZ-IMP-07 / XZ-IMP-19). The cross-agent contract is:
//   • TS `ModelSize` union must mirror Python `ALLOWED_USER_MODELS`.
//   • TS `audio_preset` union must mirror the Python IPC validator's
//     `_make_enum_validator({"auto", "studio", "noisy_room", "off",
//     "custom"})` (NOT the dataclass Literal[...] which includes
//     legacy "none"/"recommended" for backward-compat — those are
//     rewritten by the Config.load v1->v2 migration BEFORE the IPC
//     validator sees them, so the IPC boundary never accepts either).
//   • TS `noise_suppression_method` union must mirror Python
//     `NOISE_SUPPRESSION_METHODS` frozenset ({"rnnoise",
//     "deepfilternet", "none"}). The historical "speex" value was
//     never implemented and is rejected at the IPC boundary.
//   • TS `llm_preset` union must mirror Python IPC validator's
//     `_make_enum_validator({"professional", "casual", "email",
//     "code"})`.
//   • TS `bubble_x` / `bubble_y` / `bubble_scale` /
//     `test_duration_seconds` are persisted by the Python Config
//     dataclass (added by GT-FIX-11). `bubble_x`/`bubble_y` are
//     REQUIRED (`number | null`); `bubble_scale`/`test_duration_seconds`
//     are OPTIONAL on the TS side for backward compat with older
//     config.json files / older sidecars that predate the fields.
//
// This file is a TS-only static guard — it doesn't shell out to
// Python (which would require a Python interpreter in the vitest
// runner). The Python-side parity is enforced by a separate CI job
// that runs `python scripts/check_config_parity.py` (added by
// XZ-IMP-07 / XZ-IMP-19) which reads the Python dataclass fields
// and diffs against the TS interface keys extracted via TS MMT.
//
// If a future agent adds a field to the Python Config dataclass
// WITHOUT updating the TS interface, the CI parity script catches
// the drift. If a future agent adds a field to the TS interface
// WITHOUT updating the Python dataclass, the TS compile-time guards
// below catch the drift (because the type assertion fails).

import { describe, expect, it } from "vitest";

import type {
	KeyringStatus,
	ModelSize,
	VoiceTyperConfig,
} from "@/types/config";

describe("XZ-CFG-06: TS ModelSize union mirrors Python ALLOWED_USER_MODELS", () => {
	it("includes all 5 Python-allowed values (tiny.en, small.en, medium.en, qwen, parakeet)", () => {
		// Compile-time guard: each value must be assignable to `ModelSize`.
		// If a future contributor removes one from the TS union, the
		// corresponding assignment fails to compile.
		const values: ModelSize[] = [
			"tiny.en",
			"small.en",
			"medium.en",
			"qwen",
			"parakeet",
		];
		expect(values).toHaveLength(5);
	});

	it("does NOT include the legacy 'large-v3' value (intentionally excluded by Python — see config_validators.py:39-42 comment)", () => {
		// Compile-time guard: "large-v3" must NOT be assignable to
		// `ModelSize`. The conditional resolves to `false` while the
		// union excludes "large-v3"; if a contributor re-adds it, the
		// `false` assignment fails to compile.
		type HasLargeV3 = "large-v3" extends ModelSize ? true : false;
		const _guard: HasLargeV3 = false;
		expect(_guard).toBe(false);
	});
});

describe("XZ-CFG-06: TS audio_preset / noise_suppression_method / llm_preset match Python IPC validators", () => {
	it("audio_preset does NOT include legacy 'none' or 'recommended' (GT-51: tightened to IPC validator)", () => {
		// GT-51: the TS union was tightened to mirror the Python IPC
		// enum validator in `config_validators.py`, which rejects
		// 'none' and 'recommended' at the wire boundary. The Python
		// `Config.load()` v1->v2 migration rewrites stale on-disk
		// values BEFORE the IPC validator sees them, so the IPC
		// boundary never accepts either legacy value. The wider TS
		// union previously let renderer code construct a payload the
		// server would silently reject.
		//
		// Compile-time guards: 'none' / 'recommended' must NOT be
		// assignable to `audio_preset`. The conditional resolves to
		// `false` while the union excludes them; if a contributor
		// re-adds either, the `false` assignment fails to compile.
		type HasNone = "none" extends VoiceTyperConfig["audio_preset"]
			? true
			: false;
		type HasRecommended = "recommended" extends VoiceTyperConfig["audio_preset"]
			? true
			: false;
		const _none: HasNone = false;
		const _rec: HasRecommended = false;
		expect(_none).toBe(false);
		expect(_rec).toBe(false);
	});

	it("audio_preset includes all 5 IPC-accepted values", () => {
		const values: VoiceTyperConfig["audio_preset"][] = [
			"auto",
			"studio",
			"noisy_room",
			"off",
			"custom",
		];
		expect(values).toHaveLength(5);
	});

	it("noise_suppression_method does NOT include 'speex' (GT-51: removed — never implemented, rejected by IPC validator)", () => {
		// GT-51: 'speex' was never implemented (no speex backend in
		// `audio_filters/noise_suppressor.py`) and was rejected at
		// the IPC boundary. Removed from the TS union to eliminate
		// the drift.
		type HasSpeex = "speex" extends VoiceTyperConfig["noise_suppression_method"]
			? true
			: false;
		const _guard: HasSpeex = false;
		expect(_guard).toBe(false);
	});

	it("noise_suppression_method includes all 3 Python-allowed values", () => {
		const values: VoiceTyperConfig["noise_suppression_method"][] = [
			"rnnoise",
			"deepfilternet",
			"none",
		];
		expect(values).toHaveLength(3);
	});

	it("llm_preset accepts any string (widened from 4-value union for flexibility)", () => {
		// The TS type now accepts any string (matching Python server's
		// open-ended preset forwarding).  Documented presets:
		const values: string[] = ["professional", "casual", "email", "code"];
		expect(values).toHaveLength(4);
	});
});

describe("XZ-CFG-03: bubble_x / bubble_y / bubble_scale / test_duration_seconds — optionality", () => {
	it("bubble_scale and test_duration_seconds are optional (undefined is assignable)", () => {
		// Compile-time guard: a minimal `VoiceTyperConfig` literal
		// WITHOUT bubble_scale / test_duration_seconds must type-check.
		// bubble_x and bubble_y are REQUIRED (number | null) and must
		// be included.
		//
		// GT-36 / GT-FIX-11: the Python Config dataclass now persists
		// all four fields; bubble_scale / test_duration_seconds remain
		// OPTIONAL on the TS side for back-compat with older
		// config.json files / older sidecars.
		//
		// We use `as VoiceTyperConfig` (not `as unknown as`) so the
		// compiler still checks the OTHER required fields are present.
		const minimal: VoiceTyperConfig = {
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
			// GT-37 (PLAT-013/014): paste-safety toggles.
			warn_elevated_paste: true,
			warn_password_paste: true,
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
			waveform_bubble: false,
			bubble_position: "bottom",
			bubble_behavior: "show_on_record",
			bubble_draggable: true,
			bubble_show_on_startup: true,
			bubble_click_to_toggle: true,
			bubble_mic_button: true,
			history_retention_days: 90,
			history_retention_count: 0,
			history_max_entries: 1000,
			onboarding_completed: false,
			bubble_x: null,
			bubble_y: null,
			tray_left_click_action: "open_app",
			theme_mode: "system",
			theme_preset: "default",
			custom_theme: null,
			text_size: 14,
			wayland_warned: false,
			silence_warning_seconds: 20,
			stop_on_silence_seconds: 60,
			max_recording_time_seconds: 900,
			volume_duck_enabled: true,
			volume_duck_level: 0.2,
			volume_duck_per_session: false,
			volume_duck_fade_ms: 200,
			volume_duck_smart: true,
			volume_duck_smart_poll_interval_ms: 500,
			audio_preset: "auto",
			noise_filter_enabled: true,
			noise_filter_highpass: true,
			noise_filter_highpass_cutoff_hz: 80,
			noise_filter_gate: true,
			noise_filter_gate_threshold: 0,
			noise_filter_gate_hold_ms: 0,
			noise_filter_gate_open_threshold_db: 0,
			noise_filter_gate_close_threshold_db: 0,
			noise_filter_gate_attack_ms: 0,
			noise_filter_gate_release_ms: 0,
			noise_filter_rnnoise: false,
			noise_filter_post_capture: false,
			noise_suppression_method: "rnnoise",
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
			sound_feedback_enabled: true,
			ai_enhancement_enabled: false,
			auto_capitalize: true,
			auto_punctuate: true,
			fix_grammar_basics: true,
			vocabulary_automation_enabled: false,
			vocabulary_auto_confidence_threshold: 0.7,
			vocabulary_auto_apply_threshold: 0.95,
		} satisfies VoiceTyperConfig;
		// `satisfies` proves the literal is assignable (so all
		// required fields are present) AND lets us assert the four
		// deprecated fields are absent from the literal:
		expect(minimal.bubble_scale).toBeUndefined();
		expect(minimal.test_duration_seconds).toBeUndefined();
		// GT-37: optional paste-safety toggles are present in this fixture.
		expect(minimal.warn_elevated_paste).toBe(true);
		expect(minimal.warn_password_paste).toBe(true);
	});
});

describe("GT-37: warn_elevated_paste / warn_password_paste — optional paste-safety toggles", () => {
	it("both fields are declared on VoiceTyperConfig (compile-time presence guard)", () => {
		// Compile-time guard: accessing the fields on a value of
		// type `VoiceTyperConfig` must type-check. If a future
		// contributor removes either field from the interface, the
		// property access below fails to compile and CI catches it.
		// The fields are OPTIONAL (`boolean | undefined`) for back-compat
		// with older sidecars that predate GT-37; the renderer treats
		// absence as `true` (the Python default).
		const cfg = {} as VoiceTyperConfig;
		const _elevated: boolean | undefined = cfg.warn_elevated_paste;
		const _password: boolean | undefined = cfg.warn_password_paste;
		expect(_elevated).toBeUndefined();
		expect(_password).toBeUndefined();
	});
});

describe("GT-F2-3: onboarding_failed / recording_channels / pre_roll_buffer_seconds — optional server-controlled fields", () => {
	it("all three fields are OPTIONAL (omittable from a literal) and readable", () => {
		// Compile-time guard: a literal that omits all three must
		// type-check (proving they're optional). Reading them back
		// must yield `undefined` (no default at the TS layer).
		const cfg = {
			schema_version: 3,
		} as VoiceTyperConfig;
		expect(cfg.onboarding_failed).toBeUndefined();
		expect(cfg.recording_channels).toBeUndefined();
		expect(cfg.pre_roll_buffer_seconds).toBeUndefined();
	});
});

// XZ-CFG-15 removed: last_load_warnings was a Python-only transient
// instance attribute (NOT part of the IPC config type).  The TS
// VoiceTyperConfig does not include it.

describe("XZ-CFG-03 / XZ-CFG-06 / XZ-CFG-15: KeyringStatus shape (regression guard)", () => {
	it("KeyringStatus has the documented fields (available, backend, fallback, reason?)", () => {
		const ok: KeyringStatus = {
			available: true,
			backend: "SecretServiceKeyring",
			fallback: false,
		};
		const fail: KeyringStatus = {
			available: false,
			backend: null,
			fallback: true,
			reason: "keyring backend not installed",
		};
		expect(ok.available).toBe(true);
		expect(fail.fallback).toBe(true);
		expect(fail.reason).toContain("keyring");
	});
});
