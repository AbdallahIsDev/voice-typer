// Must mirror Python `ALLOWED_USER_MODELS` (derived from
// `voice_typer/server/model_registry.py`'s `MODEL_REGISTRY`). The
// Whisper catalog (pruned 2026-08-15) is `tiny` / `large-v3` /
// `large-v3-turbo` — `large-v3` was restored at the user's request
// the same day — plus `qwen` and `parakeet`; the parity test
// `types/__tests__/config-parity.test.ts` keeps the union in lockstep.
// `""` is the genuine "no model selected" state (the backend's
// `NO_MODEL_SIZE` sentinel) — the user has no active model until they
// pick one. The default is `MODEL_DEFAULT`
// (`onboarding/lib/constants.ts`), mirroring the backend's
// `DEFAULT_MODEL_SIZE`.
export type ModelSize =
	| ""
	| "tiny"
	| "large-v3"
	| "large-v3-turbo"
	| "qwen"
	| "parakeet";

/** Linux title-bar window-button customization (persisted config field
 *  `linux_window_buttons`; Settings → Appearance, Linux only). Mirrors
 *  the Python dataclass default + the `_make_linux_window_buttons_validator`
 *  shape contract (all 5 keys required, unknown keys rejected). */
export interface LinuxWindowButtonsConfig {
	mode: "system" | "custom";
	side: "left" | "right";
	show_minimize: boolean;
	show_maximize: boolean;
	show_close: boolean;
}

/** READ-ONLY computed snapshot the sidecar attaches to every `get_config`
 *  response (NOT a persisted field): the desktop's own button-layout
 *  (parsed from `gsettings org.gnome.desktop.wm.preferences button-layout`)
 *  plus the detected desktop environment (drives circle vs Breeze-square
 *  button styling). `layout` is null on non-Linux or when the probe
 *  failed. Never sent back via `set_config` — the allowlist rejects it. */
export interface LinuxWindowButtonsSystemInfo {
	desktop_environment: "gnome" | "kde" | "xfce" | "mate" | "other" | "unknown";
	layout: { side: "left" | "right"; buttons: string[] } | null;
}

export interface VoiceTyperConfig {
	schema_version: number;

	// Hotkey
	hotkey: string;

	// Recording
	sample_rate: number;
	microphone: string | null;
	//optional because older sidecars (pre-) don't
	// echo it back on `get_config`. The Python `Config` dataclass
	// declares `recording_channels: int = 1` (mono) and it's in the
	// IPC allowlist; absence on the wire is treated as 1 (mono) by
	// the renderer.
	recording_channels?: number;
	//optional because older sidecars (pre-) don't
	// echo it back. The Python `Config` dataclass declares
	// `pre_roll_buffer_seconds: float = 0.0` (no pre-roll) and it's
	// in the IPC allowlist; absence on the wire is treated as 0.0.
	pre_roll_buffer_seconds?: number;
	//auto-calibrate VAD thresholds from ambient noise
	// (server-side recording setting — no renderer UI, mirroring the
	// other `vad_*` fields which are intentionally absent from this
	// interface). Declared `vad_auto_calibrate: bool = False` in the
	// Python `Config` dataclass and IPC allowlist. OPTIONAL so older
	// sidecars that predate the field don't break the type contract.
	vad_auto_calibrate?: boolean;

	// Transcription
	model_size: ModelSize;
	language: string;
	device: "cuda" | "cpu";
	beam_size: number;
	best_of: number;
	/** Whisper-specific beam width; 1 = automatic device/model-aware default. Optional for wire tolerance with older sidecars. */
	whisper_beam_size?: number;
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
	//prewarm scheduled-task master toggle.
	fast_startup: boolean;
	//auto-update feature — runtime-pack download consent (docs/auto-update-feature.md §8.4).
	offline_pack_consent: boolean;

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

	//( / ): paste-safety toggles. Both default
	// to true in the Python `Config` dataclass
	// (`voice_typer/server/config.py:643-645`) and are in the IPC
	// allowlist. When true, the renderer surfaces a confirmation
	// dialog before pasting into elevated (admin/root) windows or
	// password fields. OPTIONAL on the TS side for backward compat
	//with older sidecars (pre-) that don't echo them; absence
	// is treated as `true` by the renderer (the Python default).
	warn_elevated_paste?: boolean;
	warn_password_paste?: boolean;

	// Recording mode
	recording_mode: "toggle" | "push_to_talk";
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

	//when in `always_visible` mode, show a mic button that toggles
	// dictation on click. Default ON.
	bubble_click_to_toggle: boolean;

	//explicit mic-button visibility toggle. Default ON. When OFF
	// the bubble stays non-interactive even in always_visible mode.
	bubble_mic_button: boolean;

	//persisted bubble position (px, relative to the active
	// screen's top-left). When non-null, the bubble window restores to
	// this position on next show; when null, the window falls back to
	// the platform-default position computed from `bubble_position`
	// ("top" / "bottom"). Both axes are stored together (either both
	// null or both non-null) — the renderer writes them as a pair via
	// `set_config({ bubble_x, bubble_y })`.
	//
	//the Python `Config` dataclass in
	// `voice_typer/server/config.py` now persists these (declared as
	// `int | None = None`) alongside `bubble_position`, and they're in
	// the IPC allowlist, so they survive across restarts via the
	// normal config.json serialisation path. Older sidecars
	//(pre-) silently dropped both keys — the renderer
	// treats absence as null. REQUIRED on the TS side because every
	//modern sidecar (post-) echoes them on `get_config`.
	bubble_x: number | null;
	bubble_y: number | null;

	//persisted bubble scale factor. Default 1.0 (no scaling).
	// The bubble window multiplies its base DPI by this value to render
	// a larger or smaller pill. Range is clamped by the renderer to
	// [0.5, 2.0] before being sent to `set_config`.
	//
	//the Python `Config` dataclass now persists
	// this as `float = 1.0` and it's in the IPC allowlist. OPTIONAL on
	// the TS side for backward compat with older config.json files /
	// older sidecars that predate the field — absence is treated as
	// 1.0 by both the renderer and the server.
	bubble_scale?: number;

	//persisted microphone-test duration (seconds). The
	// Microphone page's "Test" button records for this many seconds
	// before auto-stopping. Range clamped to [1, 30] by the server.
	//
	//the Python `Config` dataclass now persists
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
	//server-controlled flag set by the backend when the
	// onboarding flow fails irrecoverably (e.g. model download error
	// during guided setup). OPTIONAL because older sidecars
	//(pre-) don't echo it; the renderer treats absence as
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

	// Linux window-button customization (Settings → Appearance, Linux
	// only). OPTIONAL for backward compat with older sidecars that
	// predate the field — absence falls back to the resolver defaults
	// (system mode / right side / all three buttons).
	linux_window_buttons?: LinuxWindowButtonsConfig;
	// READ-ONLY computed snapshot attached by the sidecar's `get_config`
	// (desktop button-layout + DE). OPTIONAL + nullable: absent on older
	// sidecars, null layout on non-Linux / probe failure.
	linux_window_buttons_system?: LinuxWindowButtonsSystemInfo | null;

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
	//dead_air_timeout REMOVED — redundant with stop_on_silence_seconds.

	// Volume ducking
	volume_duck_enabled: boolean;
	volume_duck_level: number;
	/**
	 * @deprecated  /  / : REMOVED from the Python Config
	 * dataclass (`voice_typer/server/config.py:775-781`) — ducking now
	 * always applies to the master volume cross-platform. Existing
	 * `config.json` files that still carry the key are silently
	 * scrubbed by the v3 schema migration, so the field is NOT on the
	 * wire post-v3. Kept in the TS type as OPTIONAL for back-compat
	 * with stale on-disk config files / older sidecar responses that
	 * still echo it; renderer code MUST NOT read or write it.
	 *
	 * The field survives only as a config-file back-compat key. A
	 * future coordinated change should drop the field from the TS
	 * interface AND from every test fixture that includes it —
	 * deferred because the test fixtures are owned by other
	 * sub-agents.
	 */
	volume_duck_per_session?: boolean;
	volume_duck_fade_ms: number;
	/**
	 * @deprecated  /  / : REMOVED from the Python Config
	 * dataclass (`voice_typer/server/config.py:784-786`) — smart duck
	 * is now ALWAYS ON when `volume_duck_enabled` is True. Existing
	 * `config.json` files that still carry the key are silently
	 * scrubbed by the v3 schema migration, so the field is NOT on the
	 * wire post-v3. Kept in the TS type as OPTIONAL for back-compat
	 * with stale on-disk config files / older sidecar responses that
	 * still echo it; renderer code MUST NOT read or write it.
	 *
	 * The field survives only as a config-file back-compat key. A
	 * future coordinated change should drop the field from the TS
	 * interface AND from every test fixture that includes it —
	 * deferred because the test fixtures are owned by other
	 * sub-agents.
	 */
	volume_duck_smart?: boolean;
	volume_duck_smart_poll_interval_ms: number;

	// ADR 0007: Audio enhancement preset.
	//
	//tightened to mirror the Python IPC enum validator in
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
	/**
	 * @deprecated  / : REMOVED from the Python Config
	 * dataclass (`voice_typer/server/config.py:837-840`) — replaced
	 * by the open/close threshold pair below per ADR 0007. Existing
	 * `config.json` files that still carry the key are silently
	 * scrubbed by the v3 schema migration, so the field is NOT on
	 * the wire post-v3. Kept in the TS type as OPTIONAL for
	 * back-compat with stale on-disk config files / older sidecar
	 * responses that still echo it; renderer code MUST NOT read or
	 * write it.
	 *
	 * The field survives only as a config-file back-compat key. A
	 * future coordinated change should drop the field from the TS
	 * interface AND from every test fixture that includes it —
	 * deferred because the test fixtures are owned by other
	 * sub-agents.
	 */
	noise_filter_gate_threshold?: number; // DEPRECATED
	noise_filter_gate_hold_ms: number;
	noise_filter_gate_open_threshold_db: number;
	noise_filter_gate_close_threshold_db: number;
	noise_filter_gate_attack_ms: number;
	noise_filter_gate_release_ms: number;
	//ADR 0009: RUNTIME (server-controlled, not IPC-settable
	// per ADR 0009). The Python `Config` dataclass at
	// `voice_typer/server/config.py:842` declares
	// `noise_filter_rnnoise: bool = True` (legacy field kept for
	// back-compat with old config.json files, migrated/ignored per
	// ADR 0007 §5). It is NOT in the IPC allowlist — renderer
	// `set_config({ noise_filter_rnnoise: ... })` calls are rejected
	// by the validator. The field IS read by `level_monitor.py` and
	// synced by `config_applier.py` (which derives it from
	// `audio_preset`). The previous `// DEPRECATED` comment was
	// incorrect — this is a live runtime switch, not a deprecated
	// field.
	noise_filter_rnnoise: boolean; // RUNTIME (server-controlled, not IPC-settable per ADR 0009)
	//ADR 0009: RUNTIME (server-controlled, not IPC-settable
	// per ADR 0009). The Python `Config` dataclass at
	// `voice_typer/server/config.py:843` declares
	// `noise_filter_post_capture: bool = True` (runtime switch — see
	// ADR 0009). Actively read by `level_monitor.py` and synced by
	// `config_applier.py`. NOT in the IPC allowlist — renderer
	// `set_config({ noise_filter_post_capture: ... })` calls are
	// rejected by the validator. The previous `// DEPRECATED`
	// comment was incorrect — this is a live runtime switch, not a
	// deprecated field.
	noise_filter_post_capture: boolean; // RUNTIME (server-controlled, not IPC-settable per ADR 0009)
	//tightened to mirror the Python `NOISE_SUPPRESSION_METHODS`
	// frozenset in `voice_typer/server/config_validators.py`
	// ({"rnnoise", "gtcrn", "none"}). The historical "speex"
	// option was never implemented — there is no speex backend in
	// `audio_filters/noise_suppressor.py` — and was rejected at the
	// IPC boundary. "deepfilternet" was retired when the bundled
	// GTCRN ONNX streaming model replaced the unmaintained
	// DeepFilterNet package (the Python config loader remaps the
	// legacy on-disk value to "gtcrn"). Removed from the union to
	// eliminate the drift.
	noise_suppression_method: "rnnoise" | "gtcrn" | "none";
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

	//006/009: privacy consent flags.  All default to
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

	//sound feedback on record start/stop.  Opt-in (default
	// false).  When true, the renderer plays a short Web Audio API cue
	// when recording starts and stops — useful for accessibility and
	// for users who prefer an auditory signal.
	sound_feedback_enabled: boolean;

	//volume multiplier applied to the sound-feedback cues
	// (Settings → Recording slider + Test Sound preview). Range is
	// clamped by the server to [0.0, 1.0] — 1.0 keeps the cues'
	// baked-in level, 0.0 mutes them. OPTIONAL on the TS side for
	// backward compat with older sidecars that predate the field —
	// absence is treated as 1.0 by the renderer's sound-manager.
	sound_volume?: number;

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

	//marks that plaintext API keys have been migrated to
	// the OS keychain.  Set to true by Config.load() after the
	// first migration run.  The renderer doesn't display this
	// directly — it consults ``keyring_status`` for the user-facing
	// indicator.
	secrets_migrated?: boolean;

	//OS keychain backend status.  Attached to the
	// ``get_config`` / ``get_defaults`` IPC responses by the
	// service layer (NOT stored in the Config dataclass — it's
	// runtime-probed state).  Optional because legacy responses
	//(pre-) don't include it; the renderer treats absence
	// as "keyring unavailable, plaintext fallback".
	keyring_status?: KeyringStatus;

	//server-side load-time warnings (e.g. deprecated
	// keys scrubbed by migration, invalid values clamped to
	// defaults, missing optional files). Populated by
	// ``Config.load()`` on the Python side and attached to the
	// ``get_config`` IPC response via
	// ``_sanitize_config_for_ipc`` (which copies ``config.__dict__``
	// verbatim, so this attribute rides along). OPTIONAL because
	//older sidecars (pre-) didn't echo it; the renderer
	// treats absence as "no warnings". When non-empty, the
	// renderer surfaces a one-shot toast listing the warnings so
	// the user knows their config was migrated/clamped.
	//
	// The field is typed as ``string[] | null`` (not just
	// ``string[]``) to mirror the Python dataclass's
	// ``last_load_warnings: list[str] | None = None`` default —
	// a ``None`` value means "load() hasn't run yet" (e.g. fresh
	// defaults response) while an empty list means "load() ran
	// and produced zero warnings".
	last_load_warnings?: string[] | null;
}

/**
 * : OS keychain backend status, attached to get_config responses.
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

//003: PythonRequestType / PythonRequest / PythonResponse /
// PythonEvent types deleted. They were never imported anywhere, and
// the IPC command names were wrong ('update_config' should be
// 'set_config', 'restart' should be 'restart_app'). The actual IPC
// contract is defined by the server's _dispatch() method; the
// renderer uses untyped `call<T>(cmd, data)` which is sufficient.
