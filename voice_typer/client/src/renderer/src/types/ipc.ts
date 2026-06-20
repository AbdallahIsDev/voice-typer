import type { VoiceTyperConfig, MicrophoneDevice } from './config'

// ── Recording states ──────────────────────────────────────────────

export type RecordingState =
  | 'idle'
  | 'listening'
  | 'recording'
  | 'processing'
  | 'transcribing'
  | 'loading'
  | 'warming_up'
  | 'downloading'
  | 'paused'
  | 'cancelling'
  | 'setup'
  | 'not_configured'
  | 'error'

export type Page = 'home' | 'history' | 'templates' | 'vocabulary' | 'models' | 'microphone' | 'analytics' | 'settings' | 'onboarding'

// ── History data shapes (from Python history_db) ───────────────────

export interface HistoryRecord {
  id: number
  text: string
  timestamp: string
  duration: number
  model: string
  device: string
  word_count: number
  char_count: number
  favorite: number
  language: string
}

export interface TodayStats {
  count: number
  chars: number
  word_count: number
  duration: number
}

// ── Push events from Python (no id field) ─────────────────────────

export interface StatusChangeEvent {
  type: 'status_change'
  data: { status: string }
}

export interface ErrorEvent {
  type: 'error'
  message: string
  code?: string
}

export interface TranscriptionPartialEvent {
  type: 'transcription_partial'
  text: string
}

export interface TranscriptionFinalEvent {
  type: 'transcription_final'
  text: string
  duration_ms?: number
}

export interface RecordingStartedEvent {
  type: 'recording_started'
  timestamp: number
}

export interface RecordingStoppedEvent {
  type: 'recording_stopped'
  timestamp: number
  duration_ms?: number
}

export interface ModelLoadedEvent {
  type: 'model_loaded'
  model: string
  device: string
}

export type PythonPushEvent =
  | StatusChangeEvent
  | ErrorEvent
  | TranscriptionPartialEvent
  | TranscriptionFinalEvent
  | RecordingStartedEvent
  | RecordingStoppedEvent
  | ModelLoadedEvent

// ── Request messages (sent via window.python.call) ────────────────

export interface GetConfigRequest {
  type: 'get_config'
}

export interface UpdateConfigRequest {
  type: 'update_config'
  data: Partial<VoiceTyperConfig>
}

export interface GetMicrophonesRequest {
  type: 'get_microphones'
}

export interface ToggleDictationRequest {
  type: 'toggle_dictation'
}

export interface RestartRequest {
  type: 'restart'
}

export interface GetHistoryRequest {
  type: 'get_history'
  data?: { limit?: number; offset?: number }
}

export interface DeleteHistoryRequest {
  type: 'delete_history'
  data: { id: number }
}

export interface ClearHistoryRequest {
  type: 'clear_history'
}

export interface ToggleFavoriteRequest {
  type: 'toggle_favorite'
  data: { id: number }
}

export interface GetFavoritesRequest {
  type: 'get_favorites'
  data?: { limit?: number; offset?: number }
}

export interface SearchHistoryRequest {
  type: 'search_history'
  data: { query: string; limit?: number; offset?: number }
}

export interface GetTodayStatsRequest {
  type: 'get_today_stats'
}

export interface GetVocabularyRequest {
  type: 'get_vocabulary'
}

export interface SaveVocabularyRequest {
  type: 'save_vocabulary'
  data: Record<string, unknown>
}

export type PythonRequest =
  | GetConfigRequest
  | UpdateConfigRequest
  | GetMicrophonesRequest
  | ToggleDictationRequest
  | RestartRequest
  | GetHistoryRequest
  | DeleteHistoryRequest
  | ClearHistoryRequest
  | ToggleFavoriteRequest
  | GetFavoritesRequest
  | SearchHistoryRequest
  | GetTodayStatsRequest
  | GetVocabularyRequest
  | SaveVocabularyRequest

// ── Response data shapes (the `data` field in Python responses) ───

export interface ToggleDictationResult {
  recording: boolean
}

export interface RestartResult {
  status: string
}

export interface ToggleFavoriteResult {
  favorite: number
}

export interface SaveVocabularyResult {
  imported_categories: number
}

// ── Vocabulary types (mirrors Python VocabularyManager) ────────────

export interface VocabularyData {
  misspellings?: Record<string, string>
  technical_terms?: Record<string, string>
  names?: Record<string, string>
  products?: Record<string, string>
  phrase_corrections?: Array<[string, string]>
  extra_word_patterns?: Array<[string, string]>
}

export interface VocabularyEntry {
  category: string
  original: string
  correction: string
  index?: number
}

// ── Helper: map request type to its response data ─────────────────

export type ResponseData<T extends PythonRequest['type']> =
  T extends 'get_config' ? VoiceTyperConfig :
  T extends 'update_config' ? void :
  T extends 'get_microphones' ? MicrophoneDevice[] :
  T extends 'toggle_dictation' ? ToggleDictationResult :
  T extends 'get_history' ? HistoryRecord[] :
  T extends 'delete_history' ? void :
  T extends 'clear_history' ? void :
  T extends 'toggle_favorite' ? ToggleFavoriteResult :
  T extends 'get_favorites' ? HistoryRecord[] :
  T extends 'search_history' ? HistoryRecord[] :
  T extends 'get_today_stats' ? TodayStats :
  T extends 'get_vocabulary' ? VocabularyData :
  T extends 'save_vocabulary' ? SaveVocabularyResult :
  T extends 'restart' ? RestartResult :
  unknown

// ── Window augmentation for type-safe python bridge ───────────────

export interface PythonBridge {
  call: (msg: { type: string; data?: Record<string, unknown> }) => Promise<unknown>
  onEvent: (callback: (event: PythonPushEvent) => void) => () => void
}

// ── Window augmentation for the custom title bar (preload `window.*`) ─

export interface WindowBridge {
  minimize: () => Promise<void>
  toggleMaximize: () => Promise<boolean>
  close: () => Promise<void>
  isMaximized: () => Promise<boolean>
  onMaximizedChanged: (callback: (maximized: boolean) => void) => () => void
  exportHistory: (data: Record<string, unknown>[], format: 'json' | 'csv') => Promise<{ success: boolean; path?: string; error?: string }>
  exportVocabulary: (data: Record<string, unknown>, format: 'json' | 'csv') => Promise<{ success: boolean; path?: string; error?: string }>
}

declare global {
  interface Window {
    python?: PythonBridge
    window_?: WindowBridge
  }
}
