export type ModelSize =
	| "tiny.en"
	| "small.en"
	| "medium.en"
	| "qwen"
	| "parakeet";

export interface VoiceTyperConfig {
	schema_version: number;

	// Hotkey
	hotkey: string;

	// Recording
	sample_rate: number;
	microphone: string | null;
	// GT-F2-3: optional because older sidecars (pre-GT-F2-3) don't
	// echo it back on `get_config`. The Python `Config` dataclass
	// declares `recording_channels: int = 1` (mono) and it's in the
	// IPC allowlist; absence on the wire is treated as 1 (mono) by
	// the renderer.
	recording_channels?: number;
	// GT-F2-3: optional because older sidecars (pre-GT-F2-3) don't
	// echo it back. The Python `Config` dataclass declares
	// `pre_roll_buffer_seconds: float = 0.0` (no pre-roll) and it's
	// in the IPC allowlist; absence on the wire is treated as 0.0.
	pre_roll_buffer_seconds?: number;

	// Transcription
	model_size: ModelSize;
	language: string;
	device: "cuda" | "cpu";
	beam_size: number;
	best_of: number;
	condition_on_previous_text: boolean;

	// Hidden streaming
	streaming_transcription: boolean;
	streaming_chunk_seconds: number;
	streaming_step_seconds: number;
	streaming_left_overlap_seconds: number;
	streaming_right_guard_seconds: number;
	streaming_min_first_chunk_seconds: number;
	streaming_silence_threshold: number;

	// Behavior
	autostart: boolean;
	paste_on_stop: boolean;
	show_notifications: boolean;
	// PW-3: prewarm scheduled-task master toggle.
	fast_startup: boolean;

	// Clipboard borrow/restore (ADR-0010)
	clipboard_save_restore: boolean;
	clipboard_restore_delay_ms: number;

	// ASR backend
	asr_backend: "whisper" | "qwen" | "parakeet";
	qwen_model_path: string | null;
	parakeet_model_path: string | null;

	// Text cleanup
	text_cleanup_enabled: boolean;
	unsafe_paste_on_unknown_focus: boolean;
	corrections_path: string | null;
	log_transcriptions: boolean;

	// GT-37 (PLAT-013 / PLAT-014): paste-safety toggles. Both default
	// to true in the Python `Config` dataclass
	// (`voice_typer/server/config.py:643-645`) and are in the IPC
	// allowlist. When true, the renderer surfaces a confirmation
	// dialog before pasting into elevated (admin/root) windows or
	// password fields. OPTIONAL on the TS side for backward compat
	// with older sidecars (pre-GT-37) that don't echo them; absence
	// is treated as `true` by the renderer (the Python default).
	warn_elevated_paste?: boolean;
	warn_password_paste?: boolean;

	// Recording mode
	recording_mode: "toggle" | "push_to_talk";
	/**
	 * @deprecated EC-FIX-10 / EC-24 / GT-F2-8: server-controlled only.
	 *
	 * Kept in the type for config-file backwards-compat only — older
	 * `config.json` files written by previous versions may include
	 * this key. The server (`voice_typer/server/config.py`) declares
	 * `push_to_talk_hotkey: str = ""` but NEVER reads it —
	 * `recording_mode == "push_to_talk"` always uses the main `hotkey`
	 * field (see `voice_typer/server/config_applier.py` and
	 * `voice_typer/server/service.py`, which only check for the
	 * *presence* of the key in `updates` so the hotkey listener can
	 * be re-registered, not the value).
	 *
	 * GT-F2-8 (coordinates with GT-FIX-11 on the Python side): the
	 * field has been REMOVED from `IPC_CONFIG_ALLOWLIST` in
	 * `voice_typer/server/config_validators.py`, so the server now
	 * ENFORCES the write-only-on-the-wire contract — any
	 * `set_config({ push_to_talk_hotkey: ... })` IPC call is rejected
	 * by the validator before reaching the dataclass. The renderer
	 * cannot write this value via IPC; the field survives only as a
	 * config-file back-compat key for stale `config.json` files.
	 *
	 * Rules of engagement (EC-FIX-10, enforced as of GT-F2-8):
	 *   - The server MUST NOT read this value (the allowlist removal
	 *     now blocks any attempt to write it via IPC).
	 *   - The renderer MUST NOT write this value (no production
	 *     component sets it; only test fixtures do, for type
	 *     completeness).
	 *   - The field is kept OPTIONAL so new code can omit it entirely
	 *     without breaking the type contract.
	 *
	 * Do NOT wire up a separate PTT hotkey without also:
	 *   1. Re-adding the field to the Python IPC allowlist.
	 *   2. Reading this value in the server's hotkey listener.
	 *   3. Surfacing a UI in `RecordingSettingsSection.tsx` to set it.
	 * Until all three exist, this field is a no-op.
	 */
	push_to_talk_hotkey?: string;
	esc_cancel_enabled: boolean;
	repaste_hotkey: string;
	auto_punctuation: boolean;

	// Templates & Vocabulary
	templates_enabled: boolean;
	vocabulary_enabled: boolean;

	// Cloud ASR
	cloud_api_key: string;
	cloud_api_url: string;
	cloud_model: string;
	openai_api_key: string;
	groq_api_key: string;
	deepgram_api_key: string;

	// LLM polishing
	llm_polish: boolean;
	llm_api_key: string;
	llm_api_url: string;
	llm_model: string;
	llm_preset: string;

	// Crash recovery
	crash_recovery_enabled: boolean;

	// Audio quality
	audio_quality_warnings: boolean;
	// T020: audio_clipping_warning, audio_low_volume_warning, audio_noise_warning removed (dead code)

	// Waveform
	waveform_bubble: boolean;

	// Bubble position on screen
	bubble_position: "top" | "bottom";

	// Bubble behavior: show when recording starts, or always visible
	bubble_behavior: "show_on_record" | "always_visible";

	// Whether the bubble can be dragged by the mouse
	bubble_draggable: boolean;

	// Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
	bubble_show_on_startup: boolean;

	// UX-10: when in `always_visible` mode, show a mic button that toggles
	// dictation on click. Default ON.
	bubble_click_to_toggle: boolean;

	// UX-10: explicit mic-button visibility toggle. Default ON. When OFF
	// the bubble stays non-interactive even in always_visible mode.
	bubble_mic_button: boolean;

	// PVT-068: persisted bubble position (px, relative to the active
	// screen's top-left). When non-null, the bubble window restores to
	// this position on next show; when null, the window falls back to
	// the platform-default position computed from `bubble_position`
	// ("top" / "bottom"). Both axes are stored together (either both
	// null or both non-null) — the renderer writes them as a pair via
	// `set_config({ bubble_x, bubble_y })`.
	//
	// GT-36 / GT-FIX-11: the Python `Config` dataclass in
	// `voice_typer/server/config.py` now persists these (declared as
	// `int | None = None`) alongside `bubble_position`, and they're in
	// the IPC allowlist, so they survive across restarts via the
	// normal config.json serialisation path. Older sidecars
	// (pre-GT-FIX-11) silently dropped both keys — the renderer
	// treats absence as null. REQUIRED on the TS side because every
	// modern sidecar (post-GT-FIX-11) echoes them on `get_config`.
	bubble_x: number | null;
	bubble_y: number | null;

	// PVT-067: persisted bubble scale factor. Default 1.0 (no scaling).
	// The bubble window multiplies its base DPI by this value to render
	// a larger or smaller pill. Range is clamped by the renderer to
	// [0.5, 2.0] before being sent to `set_config`.
	//
	// GT-36 / GT-FIX-11: the Python `Config` dataclass now persists
	// this as `float = 1.0` and it's in the IPC allowlist. OPTIONAL on
	// the TS side for backward compat with older config.json files /
	// older sidecars that predate the field — absence is treated as
	// 1.0 by both the renderer and the server.
	bubble_scale?: number;

	// PVT-034: persisted microphone-test duration (seconds). The
	// Microphone page's "Test" button records for this many seconds
	// before auto-stopping. Range clamped to [1, 30] by the server.
	//
	// GT-36 / GT-FIX-11: the Python `Config` dataclass now persists
	// this as `int = 5` and it's in the IPC allowlist. OPTIONAL on the
	// TS side for backward compat with older config.json files /
	// older sidecars that predate the field — absence falls back to
	// the server default of 5s.
	test_duration_seconds?: number;

	// History
	history_retention_days: number;
	history_retention_count: number;
	history_max_entries: number;

	// Onboarding
	onboarding_completed: boolean;
	// GT-F2-3: server-controlled flag set by the backend when the
	// onboarding flow fails irrecoverably (e.g. model download error
	// during guided setup). OPTIONAL because older sidecars
	// (pre-GT-F2-3) don't echo it; the renderer treats absence as
	// false. The Python `Config` dataclass declares
	// `onboarding_failed: bool = False`.
	onboarding_failed?: boolean;

	// Tray
	tray_left_click_action: "open_app" | "toggle_dictation";

	// Theme
	theme_mode: "system" | "light" | "dark";
	theme_preset:
		| "default"
		| "amoled"
		| "nord"
		| "dracula"
		| "sepia"
		| "solarized"
		| "monokai"
		| "ayu"
		| "github"
		| "catppuccin"
		| "tokyo-night"
		| "custom";
	custom_theme: {
		light: Record<string, string>;
		dark: Record<string, string>;
	} | null;

	// Accessibility
	text_size: number;

	// Wayland
	wayland_warned: boolean;

	// Silence / max recording
	silence_warning_seconds: number;
	stop_on_silence_seconds: number;
	// SIMPLIFY-001: single explicit field replaces the old 3-field split
	// (max_recording_time_seconds_gpu, max_recording_time_seconds_cpu, and
	// max_recording_time_seconds=0 auto-selection). Now always a concrete value.
	max_recording_time_seconds: number;
	// RW-0: dead_air_timeout REMOVED — redundant with stop_on_silence_seconds.

	// Volume ducking
	volume_duck_enabled: boolean;
	volume_duck_level: number;
	volume_duck_per_session: boolean;
	volume_duck_fade_ms: number;
	volume_duck_smart: boolean;
	volume_duck_smart_poll_interval_ms: number;

	// ADR 0007: Audio enhancement preset.
	//
	// GT-51: tightened to mirror the Python IPC enum validator in
	// `voice_typer/server/config_validators.py`
	// (`_make_enum_validator({"auto", "studio", "noisy_room", "off",
	// "custom"})`). The legacy aliases "none" and "recommended" were
	// previously kept in the TS union for stale `config.json` files,
	// but the Python `Config.load()` v1→v2 migration rewrites them on
	// disk to "off" / "auto" BEFORE the IPC validator sees them, so
	// the IPC boundary never accepts either value. The wider TS union
	// let renderer code construct a payload that the Python IPC
	// validator would silently reject — narrowed to eliminate that
	// drift.
	audio_preset: "auto" | "studio" | "noisy_room" | "off" | "custom";

	// ADR 0007: Noise filtering (filter chain)
	noise_filter_enabled: boolean; // DEPRECATED
	noise_filter_highpass: boolean;
	noise_filter_highpass_cutoff_hz: number;
	noise_filter_gate: boolean;
	noise_filter_gate_threshold: number; // DEPRECATED
	noise_filter_gate_hold_ms: number;
	noise_filter_gate_open_threshold_db: number;
	noise_filter_gate_close_threshold_db: number;
	noise_filter_gate_attack_ms: number;
	noise_filter_gate_release_ms: number;
	noise_filter_rnnoise: boolean; // DEPRECATED
	noise_filter_post_capture: boolean; // DEPRECATED
	// GT-51: tightened to mirror the Python `NOISE_SUPPRESSION_METHODS`
	// frozenset in `voice_typer/server/config_validators.py`
	// ({"rnnoise", "deepfilternet", "none"}). The historical "speex"
	// option was never implemented — there is no speex backend in
	// `audio_filters/noise_suppressor.py` — and was rejected at the
	// IPC boundary, but the TS union still admitted it, letting
	// renderer code construct a payload the server would silently
	// reject. Removed from the union to eliminate the drift.
	noise_suppression_method: "rnnoise" | "deepfilternet" | "none";
	noise_filter_eq: boolean;
	noise_filter_eq_low_db: number;
	noise_filter_eq_mid_db: number;
	noise_filter_eq_high_db: number;
	noise_filter_compressor: boolean;
	noise_filter_compressor_threshold_db: number;
	noise_filter_compressor_ratio: number;
	noise_filter_compressor_attack_ms: number;
	noise_filter_compressor_release_ms: number;
	noise_filter_compressor_output_gain_db: number;
	noise_filter_limiter: boolean;
	noise_filter_limiter_ceiling_db: number;
	noise_filter_limiter_release_ms: number;
	noise_filter_notch: boolean;
	noise_filter_notch_frequency_hz: number;

	// NEW-PRIV-005/006/009: privacy consent flags.  All default to
	// false in the Python Config dataclass; the renderer must show a
	// consent dialog before flipping any of these to true.  See the
	// individual field docstrings in voice_typer/server/config.py for
	// the legal rationale (GDPR Art. 6/9/13/44, Illinois BIPA).
	huggingface_consent: boolean;
	cloud_openai_consent: boolean;
	cloud_groq_consent: boolean;
	cloud_deepgram_consent: boolean;
	voice_biometric_consent: boolean;
	// PRIVACY-001 (pre-existing): consent for sending transcribed TEXT
	// to an LLM API for polishing.  Surfaced in Settings → Privacy &
	// Consent for centralized review/revocation.
	llm_polish_consent: boolean;

	// NEW-UX-029: sound feedback on record start/stop.  Opt-in (default
	// false).  When true, the renderer plays a short Web Audio API cue
	// when recording starts and stops — useful for accessibility and
	// for users who prefer an auditory signal.
	sound_feedback_enabled: boolean;

	// P4: AI grammar / punctuation / capitalization.
	// Master toggle (ai_enhancement_enabled) defaults OFF — the user
	// must explicitly opt in via Settings → AI Enhancement.  The three
	// sub-toggles default ON so enabling the master toggle "just works".
	// See voice_typer/server/ai_enhancement.py for the implementation.
	ai_enhancement_enabled: boolean;
	auto_capitalize: boolean;
	auto_punctuate: boolean;
	fix_grammar_basics: boolean;

	// P5: Vocabulary automation (confidence-score suggestions).
	// Master toggle defaults OFF.  When ON, the dictation pipeline
	// analyzes each transcription for low-confidence words and
	// suggests vocabulary corrections; suggestions above the
	// auto-apply threshold are added to the vocabulary
	// automatically, the rest are queued for user review.
	vocabulary_automation_enabled: boolean;
	vocabulary_auto_confidence_threshold: number;
	vocabulary_auto_apply_threshold: number;

	// RW-01: marks that plaintext API keys have been migrated to
	// the OS keychain.  Set to true by Config.load() after the
	// first migration run.  The renderer doesn't display this
	// directly — it consults ``keyring_status`` for the user-facing
	// indicator.
	secrets_migrated?: boolean;

	// RW-01: OS keychain backend status.  Attached to the
	// ``get_config`` / ``get_defaults`` IPC responses by the
	// service layer (NOT stored in the Config dataclass — it's
	// runtime-probed state).  Optional because legacy responses
	// (pre-RW-01) don't include it; the renderer treats absence
	// as "keyring unavailable, plaintext fallback".
	keyring_status?: KeyringStatus;
}

/**
 * RW-01: OS keychain backend status, attached to get_config responses.
 *
 * The renderer uses this to show a "Stored securely in your OS keychain"
 * lock icon next to API key inputs (when ``available`` is true), or a
 * warning that secrets fall back to plaintext in config.json (when
 * ``available`` is false).
 */
export interface KeyringStatus {
	/** True when a real keyring backend (not the fail backend) is in use. */
	available: boolean;
	/**
	 * Backend class name (e.g. "SecretServiceKeyring", "macOSKeyring",
	 * "WindowsCredentialVaultKeyring") when available, else null.
	 */
	backend: string | null;
	/** True when secrets will be stored in plaintext in config.json. */
	fallback: boolean;
	/** Short reason string when available is false (tooltip-friendly). */
	reason?: string | null;
}

export interface MicrophoneDevice {
	index: number;
	id?: string;
	name: string;
	host_api: string;
	default?: boolean;
	channels?: number;
	rate?: number;
}

// NEW-TS-002/003: PythonRequestType / PythonRequest / PythonResponse /
// PythonEvent types deleted. They were never imported anywhere, and
// the IPC command names were wrong ('update_config' should be
// 'set_config', 'restart' should be 'restart_app'). The actual IPC
// contract is defined by the server's _dispatch() method; the
// renderer uses untyped `call<T>(cmd, data)` which is sufficient.
