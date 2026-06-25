export type ModelSize = 'tiny.en' | 'small.en' | 'medium.en' | 'qwen' | 'parakeet'

export interface VoiceTyperConfig {
  schema_version: number

  // Hotkey
  hotkey: string

  // Recording
  sample_rate: number
  microphone: string | null

  // Transcription
  model_size: ModelSize
  language: string
  device: 'cuda' | 'cpu'
  beam_size: number
  best_of: number
  condition_on_previous_text: boolean

  // Hidden streaming
  streaming_transcription: boolean
  streaming_chunk_seconds: number
  streaming_step_seconds: number
  streaming_left_overlap_seconds: number
  streaming_right_guard_seconds: number
  streaming_min_first_chunk_seconds: number
  streaming_silence_threshold: number

  // Behavior
  autostart: boolean
  paste_on_stop: boolean
  show_notifications: boolean

  // ASR backend
  asr_backend: 'whisper' | 'qwen' | 'parakeet'
  qwen_model_path: string | null
  parakeet_model_path: string | null

  // Text cleanup
  text_cleanup_enabled: boolean
  unsafe_paste_on_unknown_focus: boolean
  corrections_path: string | null
  log_transcriptions: boolean

  // Recording mode
  recording_mode: 'toggle' | 'push_to_talk'
  push_to_talk_hotkey: string
  esc_cancel_enabled: boolean
  repaste_hotkey: string
  auto_punctuation: boolean

  // Templates & Vocabulary
  templates_enabled: boolean
  vocabulary_enabled: boolean

  // Cloud ASR
  cloud_api_key: string
  cloud_api_url: string
  cloud_model: string
  openai_api_key: string
  groq_api_key: string
  deepgram_api_key: string

  // LLM polishing
  llm_polish: boolean
  llm_api_key: string
  llm_api_url: string
  llm_model: string
  llm_preset: string

  // Crash recovery
  crash_recovery_enabled: boolean

  // Audio quality
  audio_quality_warnings: boolean
  // T020: audio_clipping_warning, audio_low_volume_warning, audio_noise_warning removed (dead code)

  // Waveform
  waveform_bubble: boolean

  // Bubble position on screen
  bubble_position: 'top' | 'bottom'

  // Bubble behavior: show when recording starts, or always visible
  bubble_behavior: 'show_on_record' | 'always_visible'

  // Whether the bubble can be dragged by the mouse
  bubble_draggable: boolean

  // Whether to show the bubble at app startup (only applies when bubble_behavior is 'always_visible')
  bubble_show_on_startup: boolean

  // History
  history_retention_days: number
  history_retention_count: number
  history_max_entries: number

  // Onboarding
  onboarding_completed: boolean

  // Tray
  tray_left_click_action: 'open_app' | 'toggle_dictation'

  // Theme
  theme_mode: 'system' | 'light' | 'dark'

  // Accessibility
  high_contrast: boolean
  text_size: number

  // Wayland
  wayland_warned: boolean

  // Fast startup
  fast_startup: boolean

  // Silence / max recording
  silence_warning_seconds: number
  silence_auto_stop_seconds: number
  max_recording_seconds: number
  max_recording_seconds_gpu: number
  max_recording_seconds_cpu: number

  // Volume ducking
  volume_duck_enabled: boolean
  volume_duck_level: number
  volume_duck_per_session: boolean
  volume_duck_fade_ms: number
  volume_duck_smart: boolean
  volume_duck_smart_poll_interval_ms: number

  // Noise filtering
  noise_filter_enabled: boolean
  noise_filter_highpass: boolean
  noise_filter_highpass_cutoff_hz: number
  noise_filter_gate: boolean
  noise_filter_gate_threshold: number
  noise_filter_rnnoise: boolean
  noise_filter_post_capture: boolean

  // NEW-PRIV-005/006/009: privacy consent flags.  All default to
  // false in the Python Config dataclass; the renderer must show a
  // consent dialog before flipping any of these to true.  See the
  // individual field docstrings in voice_typer/server/config.py for
  // the legal rationale (GDPR Art. 6/9/13/44, Illinois BIPA).
  huggingface_consent: boolean
  cloud_openai_consent: boolean
  cloud_groq_consent: boolean
  cloud_deepgram_consent: boolean
  voice_biometric_consent: boolean
  // PRIVACY-001 (pre-existing): consent for sending transcribed TEXT
  // to an LLM API for polishing.  Surfaced in Settings → Privacy &
  // Consent for centralized review/revocation.
  llm_polish_consent: boolean

  // NEW-UX-029: sound feedback on record start/stop.  Opt-in (default
  // false).  When true, the renderer plays a short Web Audio API cue
  // when recording starts and stops — useful for accessibility and
  // for users who prefer an auditory signal.
  sound_feedback_enabled: boolean
}

export interface MicrophoneDevice {
  index: number
  id?: string
  name: string
  host_api: string
  default?: boolean
  channels?: number
  rate?: number
}

// NEW-TS-002/003: PythonRequestType / PythonRequest / PythonResponse /
// PythonEvent types deleted. They were never imported anywhere, and
// the IPC command names were wrong ('update_config' should be
// 'set_config', 'restart' should be 'restart_app'). The actual IPC
// contract is defined by the server's _dispatch() method; the
// renderer uses untyped `call<T>(cmd, data)` which is sufficient.
