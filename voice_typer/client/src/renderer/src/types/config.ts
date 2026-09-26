// Must mirror Python `ALLOWED_USER_MODELS` (derived from
export type ModelSize =
	| ""
	| "tiny"
	| "large-v3"
	| "large-v3-turbo"
	| "qwen"
	| "parakeet";

/**
 * Linux title-bar window-button customization (persisted config field
 * `linux_window_buttons`; Settings → Appearance, Linux only). Mirrors
 */
export interface LinuxWindowButtonsConfig {
	mode: "system" | "custom";
	side: "left" | "right";
	show_minimize: boolean;
	show_maximize: boolean;
	show_close: boolean;
}

/**
 * READ-ONLY computed snapshot the sidecar attaches to every `get_config`
 * response (NOT a persisted field): the desktop's own button-layout
 */
export interface LinuxWindowButtonsSystemInfo {
	desktop_environment: "gnome" | "kde" | "xfce" | "mate" | "other" | "unknown";
	layout: { side: "left" | "right"; buttons: string[] } | null;
}

export interface LausuConfig {
	schema_version: number;

	hotkey: string;

	sample_rate: number;
	microphone: string | null;
	//optional because older sidecars (pre-) don't
	// IPC allowlist; absence on the wire is treated as 1 (mono) by
	recording_channels?: number;
	//optional because older sidecars (pre-) don't
	pre_roll_buffer_seconds?: number;
	// Python `Config` dataclass and IPC allowlist. OPTIONAL so older
	vad_auto_calibrate?: boolean;

	model_size: ModelSize;
	language: string;
	device: "cuda" | "cpu";
	beam_size: number;
	best_of: number;
	/**
	 * Whisper-specific beam width; 1 = automatic device/model-aware default. Optional for wire tolerance with older sidecars.
	 */
	whisper_beam_size?: number;
	condition_on_previous_text: boolean;
	/**
	 * Master switch for voice-activity (silence) filtering before transcription.
	 */
	vad_filter_enabled: boolean;

	streaming_transcription: boolean;
	streaming_chunk_seconds: number;
	streaming_step_seconds: number;
	streaming_left_overlap_seconds: number;
	streaming_right_guard_seconds: number;
	streaming_min_first_chunk_seconds: number;
	streaming_silence_threshold: number;

	autostart: boolean;
	paste_on_stop: boolean;
	show_notifications: boolean;
	fast_startup: boolean;
	offline_pack_consent: boolean; // always-on; forced true on backend load

	clipboard_save_restore: boolean;
	clipboard_restore_delay_ms: number;

	asr_backend: "whisper" | "qwen" | "parakeet";
	qwen_model_path: string | null;
	parakeet_model_path: string | null;

	text_cleanup_enabled: boolean;
	unsafe_paste_on_unknown_focus: boolean;
	corrections_path: string | null;
	log_transcriptions: boolean;

	// password fields. OPTIONAL on the TS side for backward compat
	warn_elevated_paste?: boolean;
	warn_password_paste?: boolean;

	recording_mode: "toggle" | "push_to_talk";
	esc_cancel_enabled: boolean;
	repaste_hotkey: string;
	auto_punctuation: boolean;

	templates_enabled: boolean;
	vocabulary_enabled: boolean;

	cloud_api_key: string;
	cloud_api_url: string;
	cloud_model: string;
	openai_api_key: string;
	groq_api_key: string;
	deepgram_api_key: string;

	llm_polish: boolean;
	llm_api_key: string;
	llm_api_url: string;
	llm_model: string;
	llm_preset: string;

	crash_recovery_enabled: boolean;

	audio_quality_warnings: boolean;

	waveform_bubble: boolean;

	bubble_position: "top" | "bottom";

	bubble_behavior: "show_on_record" | "always_visible";

	bubble_draggable: boolean;

	bubble_show_on_startup: boolean;

	bubble_click_to_toggle: boolean;

	//explicit mic-button visibility toggle. Default ON. When OFF
	bubble_mic_button: boolean;

	bubble_x: number | null;
	bubble_y: number | null;

	// this as `float = 1.0` and it's in the IPC allowlist. OPTIONAL on
	bubble_scale?: number;

	test_duration_seconds?: number;

	history_retention_days: number;
	history_retention_count: number;
	history_max_entries: number;

	onboarding_completed: boolean;
	// during guided setup). OPTIONAL because older sidecars
	onboarding_failed?: boolean;

	tray_left_click_action: "open_app" | "toggle_dictation";

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

	// only). OPTIONAL for backward compat with older sidecars that
	linux_window_buttons?: LinuxWindowButtonsConfig;
	// (desktop button-layout + DE). OPTIONAL + nullable: absent on older
	linux_window_buttons_system?: LinuxWindowButtonsSystemInfo | null;

	text_size: number;

	wayland_warned: boolean;

	silence_warning_seconds: number;
	stop_on_silence_seconds: number;
	max_recording_time_seconds: number;

	volume_duck_enabled: boolean;
	volume_duck_level: number;
	/**
	 * dataclass (`voice_typer/server/config.py:775-781`), ducking now
	 * always applies to the master volume cross-platform. Existing
	 */
	volume_duck_per_session?: boolean;
	volume_duck_fade_ms: number;
	/**
	 * dataclass (`voice_typer/server/config.py:784-786`), smart duck
	 * is now ALWAYS ON when `volume_duck_enabled` is True. Existing
	 */
	volume_duck_smart?: boolean;
	volume_duck_smart_poll_interval_ms: number;

	audio_preset: "auto" | "studio" | "noisy_room" | "off" | "custom";

	noise_filter_enabled: boolean; // DEPRECATED
	noise_filter_highpass: boolean;
	noise_filter_highpass_cutoff_hz: number;
	noise_filter_gate: boolean;
	/**
	 * dataclass (`voice_typer/server/config.py:837-840`), replaced
	 * by the open/close threshold pair below per ADR 0007. Existing
	 */
	noise_filter_gate_threshold?: number; // DEPRECATED
	noise_filter_gate_hold_ms: number;
	noise_filter_gate_open_threshold_db: number;
	noise_filter_gate_close_threshold_db: number;
	noise_filter_gate_attack_ms: number;
	noise_filter_gate_release_ms: number;
	//ADR 0009: RUNTIME (server-controlled, not IPC-settable
	noise_filter_rnnoise: boolean; // RUNTIME (server-controlled, not IPC-settable per ADR 0009)
	//ADR 0009: RUNTIME (server-controlled, not IPC-settable
	noise_filter_post_capture: boolean; // RUNTIME (server-controlled, not IPC-settable per ADR 0009)
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

	// false in the Python Config dataclass; the renderer must show a
	huggingface_consent: boolean;
	cloud_openai_consent: boolean;
	cloud_groq_consent: boolean;
	cloud_deepgram_consent: boolean;
	voice_biometric_consent: boolean;
	llm_polish_consent: boolean;
	// ADR-0023: consent that media URLs are sent to the yt-dlp extractor.
	media_url_consent: boolean;

	sound_feedback_enabled: boolean;

	sound_volume?: number;

	// must explicitly opt in via Settings → AI Enhancement.  The three
	ai_enhancement_enabled: boolean;
	auto_capitalize: boolean;
	auto_punctuate: boolean;
	fix_grammar_basics: boolean;

	vocabulary_automation_enabled: boolean;
	vocabulary_auto_confidence_threshold: number;
	vocabulary_auto_apply_threshold: number;

	secrets_migrated?: boolean;

	// runtime-probed state).  Optional because legacy responses
	keyring_status?: KeyringStatus;

	// defaults, missing optional files). Populated by
	// verbatim, so this attribute rides along). OPTIONAL because
	last_load_warnings?: string[] | null;
}

/**
 * : OS keychain backend status, attached to get_config responses.
 *
 */
export interface KeyringStatus {
	/**
	 * True when a real keyring backend (not the fail backend) is in use.
	 */
	available: boolean;
	/**
	 * Backend class name (e.g. "SecretServiceKeyring", "macOSKeyring",
	 * "WindowsCredentialVaultKeyring") when available, else null.
	 */
	backend: string | null;
	/**
	 * True when secrets will be stored in plaintext in config.json.
	 */
	fallback: boolean;
	/**
	 * Short reason string when available is false (tooltip-friendly).
	 */
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
