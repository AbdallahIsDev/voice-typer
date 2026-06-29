import type { VoiceTyperConfig, MicrophoneDevice } from './config'

// ── Recording states ──────────────────────────────────────────────

// NEW-IPC-010: aligned with the Python ``AppState`` enum in
// ``voice_typer/server/tray_types.py``.  The previous type union
// included 7 dead values (``listening``, ``processing``, ``warming_up``,
// ``downloading``, ``paused``, ``setup``, ``not_configured``) that the
// Python backend never emits.  ``Home.tsx::statusKeyFor`` had to
// normalize ``listening`` → ``idle`` to paper over the mismatch;
// other dead values silently fell through to the default "READY"
// label, hiding real state changes from the user.
//
// The 6 values below are the only ones the backend actually emits:
//   idle, recording, transcribing, loading, cancelling, error.
export type RecordingState =
  | 'idle'
  | 'recording'
  | 'transcribing'
  | 'loading'
  | 'cancelling'
  | 'error'

export type Page = 'home' | 'history' | 'templates' | 'vocabulary' | 'models' | 'microphone' | 'analytics' | 'settings' | 'onboarding' | 'about'

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

/** Pushed after every successful set_config so the renderer can
 * update UI-local state (font-scale, theme, etc.) immediately
 * without needing a full get_config round-trip. */
export interface ConfigChangedEvent {
  type: 'config_changed'
  /** The validated subset of fields that were actually applied. */
  data: Record<string, unknown>
}

export type PythonPushEvent =
  | StatusChangeEvent
  | ErrorEvent
  | TranscriptionPartialEvent
  | TranscriptionFinalEvent
  | RecordingStartedEvent
  | RecordingStoppedEvent
  | ModelLoadedEvent
  | ConfigChangedEvent

// ── Request messages (sent via window.python.call) ────────────────

export interface GetConfigRequest {
  type: 'get_config'
}

// NEW-IPC-009 / NEW-MISMATCH-002: removed ``UpdateConfigRequest``.
// The server command is ``set_config`` (not ``update_config``), and
// the renderer uses untyped ``call<T>('set_config', data)`` directly
// — there is no consumer of this type.  Keeping a mismatched type
// (claiming ``type: 'update_config'``) gave a false impression of
// type safety while not actually being enforced anywhere.

export interface GetMicrophonesRequest {
  type: 'get_microphones'
}

export interface ToggleDictationRequest {
  type: 'toggle_dictation'
}

// ERR-IPC-004 (fix): RestartRequest was defined with type 'restart' but
// the server uses 'restart_app'. Removed the dead type — restart is
// triggered from the tray menu via the main process (stopPython sends
// quit_app), not from the renderer.
//
// NEW-IPC-009: confirmed that ``restart_app`` / ``quit_app`` are only
// sent by the Electron main process (tray menu / before-quit), never
// by the renderer.  No ``RestartRequest`` type is needed in the
// renderer's type union.

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
  | GetMicrophonesRequest
  | ToggleDictationRequest
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

// NEW-IPC-009 / NEW-MISMATCH-002: removed ``RestartResult``.
// ``restart_app`` / ``quit_app`` are not sent from the renderer (only
// the Electron main process sends them), and the server returns
// ``{type: "ack", data: {}}`` for these — there is no ``status``
// field.  The dead type gave a false impression of the response shape.

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
//
// NEW-IPC-009 / NEW-MISMATCH-002: removed the dead ``update_config``
// and ``restart`` branches.  The server's actual commands are
// ``set_config`` and ``restart_app``; the renderer uses untyped
// ``call<T>('set_config', data)`` and never sends ``restart_app`` from
// the renderer anyway.  ``set_config`` returns ``{type: "ack", data: {}}``
// on success (or ``{type: "ack", data: {accepted: [...], rejected: [...]}}``
// when some keys were silently dropped — see NEW-IPC-015 in the server).

export type ResponseData<T extends PythonRequest['type']> =
  T extends 'get_config' ? VoiceTyperConfig :
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
  // NEW-PRIV-007: GDPR right-to-export for templates + config.
  exportTemplates?: (data: unknown) => Promise<{ success: boolean; path?: string; error?: string }>
  exportConfig?: (data: unknown) => Promise<{ success: boolean; path?: string; error?: string }>
  openLogs?: () => Promise<{ success: boolean; error?: string }>
}

// ── Bubble bridge API (exposed by Electron preload for the bubble overlay) ─

export interface WindowBubble {
  // Commands (main process → bubble window) — optional because the
  // bubble overlay may not be fully initialized when called from App.tsx
  signalReady?: () => void
  setPosition?: (pos: string) => void
  setDraggable?: (v: boolean) => void
  show?: () => void
  hide?: () => void
  setLevel?: (level: number) => void
  // NEW-A11Y-006: keyboard-based move (accessibility alternative to drag).
  moveBy?: (deltaX: number, deltaY: number) => void
  // Event subscriptions (bubble window → main process) — always present
  // when the bubble window is loaded (exposed by the preload script)
  onLevel: (cb: (data: { rms: number; peak: number }) => void) => () => void
  onShow: (cb: () => void) => () => void
  onHide: (cb: () => void) => () => void
  onDraggable: (cb: (draggable: boolean) => void) => () => void
  hideComplete: () => void
  // Auto-resize the BrowserWindow to exactly fit the pill content,
  // eliminating the transparent dead zone around the bubble.
  resizeTo?: (width: number, height: number) => void
}

declare global {
  interface Window {
    python?: PythonBridge
    window_?: WindowBridge
    bubble?: WindowBubble
  }
}
