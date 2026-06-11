// src/renderer/src/types/config.ts

export interface VoiceTyperConfig {
  hotkey: string
  microphone: string | null
  model_size: 'tiny.en' | 'small.en' | 'medium.en' | 'qwen'
  language: string
  device: 'cuda' | 'cpu'
  beam_size: number
  best_of: number
  condition_on_previous_text: boolean
  streaming_transcription: boolean
  autostart: boolean
  paste_on_stop: boolean
  show_notifications: boolean
  text_cleanup_enabled: boolean
  unsafe_paste_on_unknown_focus: boolean
  log_transcriptions: boolean
  asr_backend: 'whisper' | 'qwen'
  qwen_model_path: string
  corrections_path: string
  silence_warning_seconds: number
  silence_auto_stop_seconds: number
  max_recording_seconds: number
  max_recording_seconds_gpu: number
  max_recording_seconds_cpu: number
  schema_version: number
}

export interface MicrophoneDevice {
  index: number
  name: string
  host_api: string
}

export type PythonRequestType =
  | 'get_config'
  | 'update_config'
  | 'get_microphones'
  | 'restart'

export interface PythonRequest {
  id: string
  type: PythonRequestType
  data?: Record<string, unknown>
}

export interface PythonResponse {
  id: string
  type: string
  success: boolean
  data?: unknown
  error?: string
}

export interface PythonEvent {
  type: string
  data?: Record<string, unknown>
}
