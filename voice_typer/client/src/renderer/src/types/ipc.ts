// src/renderer/src/types/ipc.ts

import type { VoiceTyperConfig, MicrophoneDevice } from './config'

// ── Recording states ──────────────────────────────────────────────

export type RecordingState = 'idle' | 'listening' | 'recording' | 'processing' | 'error'

export type Page = 'home' | 'history' | 'settings'

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
  data?: { limit?: number }
}

export interface GetTodayStatsRequest {
  type: 'get_today_stats'
}

export type PythonRequest =
  | GetConfigRequest
  | UpdateConfigRequest
  | GetMicrophonesRequest
  | ToggleDictationRequest
  | RestartRequest
  | GetHistoryRequest
  | GetTodayStatsRequest

// ── Response data shapes (the `data` field in Python responses) ───

export interface ToggleDictationResult {
  recording: boolean
}

export interface RestartResult {
  status: string
}

// ── Helper: map request type to its response data ─────────────────

export type ResponseData<T extends PythonRequest['type']> =
  T extends 'get_config' ? VoiceTyperConfig :
  T extends 'update_config' ? void :
  T extends 'get_microphones' ? MicrophoneDevice[] :
  T extends 'toggle_dictation' ? ToggleDictationResult :
  T extends 'get_history' ? HistoryRecord[] :
  T extends 'get_today_stats' ? TodayStats :
  T extends 'restart' ? RestartResult :
  unknown

// ── Window augmentation for type-safe python bridge ───────────────

export interface PythonBridge {
  call: (msg: { type: string; data?: Record<string, unknown> }) => Promise<unknown>
  onEvent: (callback: (event: PythonPushEvent) => void) => () => void
}

declare global {
  interface Window {
    python?: PythonBridge
  }
}
