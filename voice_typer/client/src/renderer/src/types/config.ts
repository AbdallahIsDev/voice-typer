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

	// Recording mode
	recording_mode: "toggle" | "push_to_talk";
	push_to_talk_hotkey: string;
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

	// History
	history_retention_days: number;
	history_retention_count: number;
	history_max_entries: number;

	// Onboarding
	onboarding_completed: boolean;

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

	// ADR 0007: Audio enhancement preset
	audio_preset:
		| "auto"
		| "studio"
		| "noisy_room"
		| "off"
		| "custom"
		| "none"
		| "recommended";

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
	noise_suppression_method: "rnnoise" | "deepfilternet" | "speex" | "none";
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
