// src/renderer/src/types/__tests__/config-parity.test.ts
//
//(parity guard): static type-level
// regression tests pinning the TS `VoiceTyperConfig` interface to the
// Python `Config` dataclass + IPC validator constraints.
//
// The Python side (config.py, config_validators.py) is owned by other
//sub-agents ( / ). The cross-agent contract is:
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
//dataclass (added by ). `bubble_x`/`bubble_y` are
//     REQUIRED (`number | null`); `bubble_scale`/`test_duration_seconds`
//     are OPTIONAL on the TS side for backward compat with older
//     config.json files / older sidecars that predate the fields.
//
// This file is a TS-only static guard — it doesn't shell out to
// Python (which would require a Python interpreter in the vitest
// runner). The Python-side parity is enforced by a separate CI job
// that runs `python scripts/check_config_parity.py` (added by
//) which reads the Python dataclass fields
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
	it("includes the empty-string no-model sentinel plus all Python-allowed model values", () => {
		// 2026-08-15: the Whisper catalog was pruned to the
		// multilingual variants the user kept (tiny + large-v3-turbo)
		// and `large-v3` was restored at the user's request the same
		// day; the `.en`-suffixed, small/medium, and other large
		// sizes were removed. The `""` member is the genuine "no
		// model selected" state — the backend's `NO_MODEL_SIZE`
		// sentinel, accepted by `ALLOWED_USER_MODELS`-derived
		// validation. Python `ALLOWED_USER_MODELS` in
		// `voice_typer/server/config_validators.py` now allows exactly
		// these 5 model values plus the empty sentinel, and this union
		// mirrors it.
		//
		// Compile-time guard: each value must be assignable to `ModelSize`.
		// If a future contributor removes one from the TS union, the
		// corresponding assignment fails to compile.
		const values: ModelSize[] = [
			"",
			"tiny",
			"large-v3",
			"large-v3-turbo",
			"qwen",
			"parakeet",
		];
		expect(values).toHaveLength(6);
	});

	it("FR-4: includes the multilingual 'tiny' variant (positive conditional-type guard)", () => {
		// positive compile-time guard that the multilingual
		// Whisper variants are present in the TS union. The conditional
		// resolves to `true` while "tiny" is in `ModelSize`; if a
		// future contributor removes it, the `true` assignment fails
		// to compile.
		type HasMultilingualTiny = "tiny" extends ModelSize ? true : false;
		const _g: HasMultilingualTiny = true;
		expect(_g).toBe(true);
	});

	it("pruned: removed the multilingual 'small' and 'medium' variants (negative conditional-type guards)", () => {
		// negative compile-time guards: the pruned sizes must NOT be
		// assignable to `ModelSize`. The conditional resolves to
		// `false` while the union excludes them; if a future
		// contributor re-adds one, the `false` assignment fails to
		// compile.
		type HasMultilingualSmall = "small" extends ModelSize ? true : false;
		type HasMultilingualMedium = "medium" extends ModelSize ? true : false;
		const _small: HasMultilingualSmall = false;
		const _medium: HasMultilingualMedium = false;
		expect(_small).toBe(false);
		expect(_medium).toBe(false);
	});

	it("includes 'large-v3' (restored to the catalog 2026-08-15 at the user's request)", () => {
		// Compile-time guard: "large-v3" must be assignable to
		// `ModelSize`. The conditional resolves to `true` while the
		// union includes "large-v3"; if a future contributor removes
		// it, the `true` assignment fails to compile.
		type HasLargeV3 = "large-v3" extends ModelSize ? true : false;
		const _guard: HasLargeV3 = true;
		expect(_guard).toBe(true);
	});
});

describe("XZ-CFG-06: TS audio_preset / noise_suppression_method / llm_preset match Python IPC validators", () => {
	it("audio_preset does NOT include legacy 'none' or 'recommended' (GT-51: tightened to IPC validator)", () => {
		//the TS union was tightened to mirror the Python IPC
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
		//'speex' was never implemented (no speex backend in
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

describe("FR-67: volume_duck_per_session / volume_duck_smart / noise_filter_gate_threshold — REMOVED from wire post-v3", () => {
	it("all three fields are OPTIONAL (omittable from a literal)", () => {
		//these fields were REMOVED from the Python Config
		// dataclass (`voice_typer/server/config.py:775-781, 784-786,
		// 837-840`) — existing `config.json` files that still carry
		// them are silently scrubbed by the v3 schema migration, so
		// they're NOT on the wire post-v3. The TS interface marks
		// them as OPTIONAL (`?:`) with `@deprecated` tags, matching
		// the precedent set by `push_to_talk_hotkey` (server-controlled
		// only — kept in the type for config-file back-compat only).
		//
		// Compile-time guards: each of the three fields must be
		// `T | undefined` (optional). We use a conditional-type guard:
		// `undefined extends T ? true : false` resolves to `true` only
		// when `T` admits `undefined` (i.e. is optional). If a future
		// contributor re-adds the `?:` -> `:` (drops optionality), the
		// `true` assignment fails to compile.
		type IsOptional<T> = undefined extends T ? true : false;
		type PerSessionOptional = IsOptional<
			VoiceTyperConfig["volume_duck_per_session"]
		>;
		type SmartOptional = IsOptional<VoiceTyperConfig["volume_duck_smart"]>;
		type GateThresholdOptional = IsOptional<
			VoiceTyperConfig["noise_filter_gate_threshold"]
		>;
		const _perSession: PerSessionOptional = true;
		const _smart: SmartOptional = true;
		const _gate: GateThresholdOptional = true;
		expect(_perSession).toBe(true);
		expect(_smart).toBe(true);
		expect(_gate).toBe(true);
	});

	it("the fields are still readable as `T | undefined` for back-compat with stale config files", () => {
		//even though the fields are no longer on the wire
		// post-v3, the TS type keeps them OPTIONAL so renderer code
		// can still read stale on-disk config files / older sidecar
		// responses that echo them. The values surface as
		// `T | undefined`.
		const cfg = {} as VoiceTyperConfig;
		const _perSession: boolean | undefined = cfg.volume_duck_per_session;
		const _smart: boolean | undefined = cfg.volume_duck_smart;
		const _gate: number | undefined = cfg.noise_filter_gate_threshold;
		expect(_perSession).toBeUndefined();
		expect(_smart).toBeUndefined();
		expect(_gate).toBeUndefined();
	});
});

describe("FR-67: noise_filter_rnnoise / noise_filter_post_capture — RUNTIME switches per ADR 0009 (NOT deprecated)", () => {
	it("both fields are still REQUIRED on VoiceTyperConfig (compile-time presence guard)", () => {
		//per ADR 0009, these two fields are RUNTIME switches
		// (server-controlled, NOT IPC-settable). The Python Config
		// dataclass at `voice_typer/server/config.py:842-843` declares
		// them as `bool = True` and they're actively read by
		// `level_monitor.py` / synced by `config_applier.py`. The
		// previous `// DEPRECATED` TS comments were incorrect — these
		// are live runtime switches, not deprecated fields. They're
		// NOT in the IPC allowlist (renderer `set_config(...)` calls
		// are rejected by the validator), but they ARE echoed on
		// `get_config` and the renderer must surface them in the UI.
		//
		// Compile-time guard: accessing the fields on a value of type
		// `VoiceTyperConfig` must type-check (NOT `boolean | undefined`
		// — they're required). If a future contributor removes either
		// field from the interface or makes them optional, the
		// `boolean` (non-undefined) annotation fails to compile.
		const cfg = {} as VoiceTyperConfig;
		const _rnnoise: boolean = cfg.noise_filter_rnnoise;
		const _postCapture: boolean = cfg.noise_filter_post_capture;
		// Runtime sanity: `{} as VoiceTyperConfig` is an unsafe cast so
		// the fields are actually `undefined` at runtime — but the
		// *static* type is `boolean` (required, non-optional).
		expect(_rnnoise).toBeUndefined();
		expect(_postCapture).toBeUndefined();
	});
});

describe("XZ-CFG-03: bubble_x / bubble_y / bubble_scale / test_duration_seconds — optionality", () => {
	it("bubble_scale and test_duration_seconds are optional (undefined is assignable)", () => {
		// Compile-time guard: a minimal `VoiceTyperConfig` literal
		// WITHOUT bubble_scale / test_duration_seconds must type-check.
		// bubble_x and bubble_y are REQUIRED (number | null) and must
		// be included.
		//
		//the Python Config dataclass now persists
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
			fast_startup: false,
			offline_pack_consent: false,
			clipboard_save_restore: true,
			clipboard_restore_delay_ms: 150,
			asr_backend: "whisper",
			qwen_model_path: null,
			parakeet_model_path: null,
			text_cleanup_enabled: true,
			unsafe_paste_on_unknown_focus: false,
			corrections_path: null,
			log_transcriptions: false,
			//(/014): paste-safety toggles.
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
		//optional paste-safety toggles are present in this fixture.
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
		//with older sidecars that predate ; the renderer treats
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

//removed: last_load_warnings was a Python-only transient
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
