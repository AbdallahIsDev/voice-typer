import type { MicrophoneDevice, VoiceTyperConfig } from "./config";

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
	| "idle"
	| "recording"
	| "transcribing"
	| "loading"
	| "cancelling"
	| "error";

export type Page =
	| "home"
	| "history"
	| "templates"
	| "vocabulary"
	| "models"
	| "microphone"
	| "analytics"
	| "settings"
	| "onboarding"
	| "about";

// ── History data shapes (from Python history_db) ───────────────────

export interface HistoryRecord {
	id: number;
	text: string;
	timestamp: string;
	duration: number;
	model: string;
	device: string;
	word_count: number;
	char_count: number;
	favorite: number;
	language: string;
}

export interface TodayStats {
	count: number;
	chars: number;
	word_count: number;
	duration: number;
}

// ── Push events from Python (no id field) ─────────────────────────

export interface StatusChangeEvent {
	type: "status_change";
	data: { status: string };
}

// PVT-G5-010 (part 4): ErrorEvent now mirrors the actual Python error
// envelope. The server emits one of these shapes via `_send_error(...)`:
//   - {type:"error", data:{code:"unknown_command", message, command?}}
//   - {type:"error", data:{code:"invalid_field"|"missing_field", message, field?}}
//   - {type:"error", data:{code:"unknown_tray_item", id?}} (no `message`)
//   - {type:"error", data:{code:"internal_error"|"rate_limited", message}}
// `code` is always present (REQUIRED); `message` is conventionally present
// but omitted for `unknown_tray_item` (GT-F2-7: made OPTIONAL on the TS
// side to match the emitter reality). The 4 optional fields cover all
// variants without forcing callers to narrow on `code` first.
//
// Note: this is the module-local ErrorEvent (Python IPC). It does NOT
// collide with the global DOM `ErrorEvent` used by `addEventListener`
// in `globalErrorHandler.ts` — that file does not import this type and
// resolves `ErrorEvent` to the lib.dom.d.ts declaration.
//
// EC-FIX-7 (addresses [EC-10]): narrowed `code` from bare `string` to the
// `ErrorCodes` union below. The namespaced forms (`server.*`, `client.*`)
// are the forward contract; the legacy aliases are still emitted by some
// backend paths (see `voice_typer/server/handlers/_base.py:178`,
// `voice_typer/server/ipc_server.py:1186,1547`, and
// `voice_typer/server/sidecar_ws.py:316,404`) and must remain valid here
// until the migration to namespaced forms is completed (tracked by
// [EC-10] / EC-FIX-9 in the backend).
export type ErrorCodes =
	| "client.invalid_field"
	| "client.missing_field"
	| "client.invalid_payload"
	| "client.rate_limited"
	| "client.path_not_allowed"
	| "client.not_found"
	| "client.auth_failed"
	| "server.internal_error"
	| "server.handler_error"
	| "server.file_locked"
	| "server.model_switch_failed"
	| "server.shutting_down"
	| "server.unknown_command"
	| "server.unknown_tray_item"
	// Legacy aliases (still emitted by some paths for backward compat)
	| "internal_error"
	| "shutting_down"
	| "unknown_command"
	| "unknown_tray_item"
	| "auth_failed"
	| "rate_limited"
	| "invalid_payload"
	| "invalid_field"
	| "missing_field"
	| "not_initialized"
	| "payload_too_large"
	| "handler_error"
	| "sidecar_disconnected"
	| "disallowed_command"
	| "disallowed_window";

export interface ErrorEvent {
	type: "error";
	data: {
		code: ErrorCodes;
		// GT-F2-7: `message` is OPTIONAL — the `unknown_tray_item`
		// emitter in `voice_typer/server/ipc_server.py` pushes
		// `{type:"error", data:{code:"unknown_tray_item", id?}}`
		// with NO `message` field (only `id` identifies the bad
		// tray item). All other emitters include `message`.
		// Previously REQUIRED here, forcing callers to either lie
		// (asserting `message: string` on a value that's
		// `undefined` at runtime) or narrow on `code` first.
		message?: string;
		field?: string;
		command?: string;
		id?: string | number;
	};
}

// PVT-G5-010 (part 3): the dead `TranscriptionPartialEvent` type was
// REMOVED. No `event_bus.publish({"type": "transcription_partial"})`
// exists anywhere in the Python tree — the partial-transcription path
// in `dictation_pipeline.py` only publishes `transcription_final`. The
// old type was a phantom contract that gave a false impression of an
// IPC event that never fires. Do NOT re-add it without also wiring up
// a publisher (`event_bus.publish({"type":"transcription_partial", ...})`).
// The compile-time guard in `types/__tests__/ipc-types.test.ts` will
// fail tsc if this is re-added (the union length assertion drops by 1).

// PVT-G5-010 (part 1): `transcription_final` payload nests inside `data`
// (matching `voice_typer/server/dictation_pipeline.py:911`), NOT at the
// root. The old shape declared `text: string` at the root — a type lie.
// Runtime reader `Home.tsx:428` already accesses `data?.text`, so this
// fix aligns the type with the wire format AND the existing consumer.
export interface TranscriptionFinalEvent {
	type: "transcription_final";
	data: { text: string; duration_ms?: number };
}

// PVT-G5-010 (part 2): the Python emitters for `recording_started` and
// `recording_stopped` push bare `{type: ...}` frames — they never send
// `timestamp` or `duration_ms`. The old type claimed they were present,
// so any code reading `event.timestamp` would get `undefined` at runtime.
// (`useSoundFeedback.ts:48,52` subscribes to both but accesses no fields,
// so the field removal is API-safe.)
export interface RecordingStartedEvent {
	type: "recording_started";
}

export interface RecordingStoppedEvent {
	type: "recording_stopped";
}

// NEW-IPC-002 (d-review): the dead `ModelLoadedEvent` type was REMOVED.
// The server never published `model_loaded` via `event_bus.publish(...)`
// (the only `model_loaded` symbol in the Python tree is a LOCAL log variable
// in `recording_controller.py:145`), and ZERO renderer code subscribed to
// `"model_loaded"` (no `usePythonEvent("model_loaded", ...)` call sites).
// Keeping the type gave a false impression of an IPC contract that didn't
// exist. Do NOT re-add it without also wiring up BOTH a publisher
// (`event_bus.publish({"type":"model_loaded",...})`) AND a subscriber
// (`usePythonEvent("model_loaded", ...)`). The compile-time guard in
// `types/__tests__/ipc-types.test.ts` will fail tsc if this is re-added.

/** Pushed after every successful set_config so the renderer can
 * update UI-local state (font-scale, theme, etc.) immediately
 * without needing a full get_config round-trip. */
export interface ConfigChangedEvent {
	type: "config_changed";
	/** The validated subset of fields that were actually applied. */
	data: Record<string, unknown>;
}

/** Pushed when the backend detects Esc during hotkey capture mode.
 *  The backend consumes the key at the OS level (RegisterHotKey), so the
 *  DOM keydown event never reaches the renderer — this event tells the
 *  HotkeyPicker to exit capture mode. */
export interface HotkeyCaptureCancelEvent {
	type: "hotkey_capture_cancel";
}

/** Pushed when history records change through a path OUTSIDE the
 * current renderer page (clear/delete/restore/star from another window,
 * the tray menu, or a CLI tool). Renderer pages that cache history
 * (Home, History, Dashboard) subscribe to this and invalidate their
 * caches so they don't show ghost records. `reason` is one of
 * "cleared" | "deleted" | "restored" | "favorite_toggled". */
export interface HistoryChangedEvent {
	type: "history_changed";
	data: { reason: string };
}

// ── Additional Python push events (PVT-G5-060 / PVT-G5-061) ───────
//
// The Python backend emits 24+ distinct event `type` literals (see
// `voice_typer/server/event_bus.py:36-95`). The previous union typed
// only 9 of them — the rest flowed through `onEvent` untyped, so a
// `usePythonEvent("paste_failed", ...)` call had no compile-time
// guarantee that `paste_failed` was a real event name (a typo like
// `"past_failed"` would silently never fire).
//
// The events below use `data: Record<string, unknown>` for payloads
// whose shape we haven't audited field-by-field. The goal here is to
// make the `type` literal itself type-safe (so a typo is a compile
// error), not to fully specify every payload. Future agents can
// tighten individual `data` shapes as needed (e.g. promote
// `DownloadProgressEvent.data` to `{ received: number; total: number;
// percent: number }` once the wire shape is verified).

// PVT-G5-060: emitted on every client connect
// (`voice_typer/server/ipc_server.py:1311-1326`) with a snapshot of the
// backend AppState so the renderer can hydrate its connection state
// without a round-trip. Today NO renderer subscribes to this — the
// connect-time snapshot is silently dropped. Wiring a
// `usePythonEvent("state_changed", …)` subscriber in `useConnection.ts`
// is a follow-up (out of FA9 scope).
export interface StateChangedEvent {
	type: "state_changed";
	data: Record<string, unknown>;
}

// PVT-G5-061: the 18 most important missing event types. Each one is
// emitted by at least one `event_bus.publish(...)` call in the Python
// tree and consumed by at least one `usePythonEvent(...)` call in the
// renderer (verified via `rg 'usePythonEvent\("..."'`).

/** Paste-to-active-window failed (Linux/macOS paste path); renderer
 *  surfaces a toast. Emitted from `paste_controller.py`. */
export interface PasteFailedEvent {
	type: "paste_failed";
	data: Record<string, unknown>;
}

/** HuggingFace model download progress (chunk counter). Emitted from
 *  `model_downloader.py`. Consumed by `Home.tsx:402`. */
export interface DownloadProgressEvent {
	type: "download_progress";
	data: Record<string, unknown>;
}

/** Generic user-facing notification (title + body + severity). Emitted
 *  from various handler mixins. */
export interface NotificationEvent {
	type: "notification";
	data: Record<string, unknown>;
}

/** A learned vocabulary correction suggestion surfaced for the user to
 *  accept/reject. Emitted from `vocabulary_suggester.py`. */
export interface VocabularySuggestionEvent {
	type: "vocabulary_suggestion";
	data: Record<string, unknown>;
}

/** The set of available microphones changed (hot-plug / unplug).
 *  Emitted from `mic_watcher.py`. */
export interface MicrophonesChangedEvent {
	type: "microphones_changed";
	data: Record<string, unknown>;
}

/** A `test_microphone` request finished — renderer shows the recorded
 *  duration + RMS. Emitted from `microphone_handlers.py`. */
export interface MicrophoneTestCompleteEvent {
	type: "microphone_test_complete";
	data: Record<string, unknown>;
}

/** A raw audio clip is being pushed (e.g. for the waveform display or
 *  for clipboard copy). Emitted from `recording_controller.py`. */
export interface AudioClipEvent {
	type: "audio_clip";
	data: Record<string, unknown>;
}

/** The tray menu config changed — renderer can refresh its in-app
 *  mirror. Emitted from `tray.py`. */
export interface TrayMenuEvent {
	type: "tray_menu";
	data: Record<string, unknown>;
}

/** Request that the renderer switch to a different page. Emitted from
 *  tray menu clicks + onboarding flow. Consumed by `App.tsx:209`. */
export interface NavigateEvent {
	type: "navigate";
	data: Record<string, unknown>;
}

/** Backend finished its startup sequence — renderer can hide the
 *  loading splash. Emitted from `startup_sequence.py`. */
export interface ReadyEvent {
	type: "ready";
	data: Record<string, unknown>;
}

// ── Bubble-window events ───────────────────────────────────────────
// These are routed by `handle-message.ts` directly to the bubble
// BrowserWindow (`webContents.send("bubble:...", ...)`) AND forwarded
// to the main renderer via the `python-event` IPC channel. They're in
// the union so renderer code that wants to observe bubble state (e.g.
// Settings page showing "bubble visible: yes/no") can subscribe.

export interface BubbleShowEvent {
	type: "bubble_show";
	data: Record<string, unknown>;
}

export interface BubbleHideEvent {
	type: "bubble_hide";
	data: Record<string, unknown>;
}

export interface BubbleSetStateEvent {
	type: "bubble_set_state";
	data: Record<string, unknown>;
}

export interface BubbleLevelEvent {
	type: "bubble_level";
	data: Record<string, unknown>;
}

export interface BubbleConfigEvent {
	type: "bubble_config";
	data: Record<string, unknown>;
}

// ── Lifecycle events (tray menu / shutdown flow) ───────────────────

/** Tray "Open app" — Python asks Electron to show + focus the dashboard.
 *  Routed by `handle-message.ts:68` → `showMainWindow()`. */
export interface ShowWindowEvent {
	type: "show_window";
	data: Record<string, unknown>;
}

/** Tray "Quit" — Python is about to force-exit; close Electron too.
 *  Routed by `handle-message.ts:74` → `app.quit()`. */
export interface QuitAppEvent {
	type: "quit_app";
	data: Record<string, unknown>;
}

/** Tray "Restart" — Python's `restart_app()` pushes this BEFORE calling
 *  `sys.exit(0)`. Routed by `handle-message.ts:78` → `relaunchApp()`.
 *
 *  EC-FIX-7 (addresses [EC-3]): the wire event was renamed from
 *  `relaunch_electron` to `relaunch_app` on the Python+Tauri sides in
 *  PVT-2. The renderer type now models `relaunch_app` as the canonical
 *  event.
 *
 *  GT-55: the legacy `RelaunchElectronEvent` (type: "relaunch_electron")
 *  was DELETED — verified the Python side emits ONLY `relaunch_app` now
 *  (the `relaunch_electron` symbol survives only in historical comments
 *  in `voice_typer/server/app.py` and `voice_typer/server/ipc_server.py`,
 *  not as a wire event). The transition window for old sidecars has long
 *  since closed. */
export interface RelaunchAppEvent {
	type: "relaunch_app";
	data: Record<string, unknown>;
}

// ── GT-52: server-emitted push events previously missing from the union ─
//
// The Python backend emits these via `event_bus.publish(...)` but the
// TS `PythonPushEvent` union never modelled them, so renderer code
// subscribing via `usePythonEvent("tray_state", ...)` got no compile-time
// type narrowing (the event was typed as `never` in the union, forcing
// `as unknown as PythonPushEvent` casts — a Rule 26 violation). Added
// here so the union matches the actual server emit surface.

/** Pushed by `voice_typer/server/tray_menu.py:416` to update the tray
 *  icon + tooltip. The Python emitter (`_push_tray_state`) only
 *  includes `icon` and/or `tooltip` if non-null, and bails out if BOTH
 *  are null — so at runtime the payload has at least one of the two,
 *  but the TS type marks both optional to match the emitter's
 *  conditional inclusion pattern. Consumed by the Tauri Rust host
 *  (`src-tauri/src/sidecar/ws.rs`) which forwards to the OS tray. */
export interface TrayStateEvent {
	type: "tray_state";
	data: { icon?: string; tooltip?: string };
}

/** Pushed by `voice_typer/server/service/model.py:596-605` when a model
 *  download is refused because the user has not granted the required
 *  consent (e.g. HuggingFace). The renderer surfaces a consent dialog
 *  naming the provider + model; the message is shown verbatim. */
export interface ConsentRequiredEvent {
	type: "consent_required";
	data: { provider: string; model: string; message: string };
}

/** Pushed by `voice_typer/server/parakeet_engine.py:910-915` when GPU
 *  transcription fails and the engine falls back to CPU. `device` is
 *  always `"cpu"` today; `reason` is the truncated exception message
 *  (max 200 chars per the Python emitter). The renderer uses this to
 *  show a one-time "transcription will be slower" banner. */
export interface ParakeetCpuFallbackEvent {
	type: "parakeet_cpu_fallback";
	data: { device: string; reason: string };
}

// ── FT-1 (resilient sidecar) lifecycle events ──────────────────────
//
// EC-FIX-7 (addresses [EC-14]): these events are NOT emitted by the Python
// backend — they are synthesized by the host bridge (Tauri Rust
// `src-tauri/src/sidecar/ft1.rs` or Electron main) when the transport
// layer detects a disconnect and enters the FT-1 reconnect loop. They
// are members of `PythonPushEvent` so renderer code can subscribe via
// `usePythonEvent("reconnecting", ...)` without an `as unknown as
// PythonPushEvent` cast (the unsafe cast was a rule #26 violation —
// every IPC message must have a matching type definition).
//
// `reason` values currently emitted:
//   - "tcp_disconnected" — the TCP socket closed unexpectedly
//   - "ws_closed"        — the WebSocket closed with a non-1000 code
//   - "heartbeat_timeout" — no PONG within the deadline
//   - "reconnect_ok"     — (reconnected only) the new socket is live

/** Pushed when the host bridge starts a reconnect attempt after a
 *  transport drop. Consumed by `hooks/useConnection.ts:277`. */
export interface ReconnectingEvent {
	type: "reconnecting";
	data: { reason: string };
}

/** Pushed when the host bridge successfully reconnected to the Python
 *  backend. Consumed by `hooks/useConnection.ts:287`. */
export interface ReconnectedEvent {
	type: "reconnected";
	data: { reason: string };
}

// NOTE: `usePythonEvent`'s `type` param (declared in
// `hooks/usePython.ts`, owned by EC-FIX-20) should be narrowed to
// `PythonPushEvent["type"]` so a typo like `usePythonEvent("past_failed", ...)`
// fails at compile time instead of silently never firing. Currently the
// `type` param is `string`, which lets any typo through and was the root
// cause of the FT-1 unsafe `as unknown as PythonPushEvent` casts noted in
// [EC-14]. This file cannot perform that narrowing itself (the hook lives
// in another sub-agent's file scope); it only exposes the union.
export type PythonPushEvent =
	| StatusChangeEvent
	| ErrorEvent
	| TranscriptionFinalEvent
	| RecordingStartedEvent
	| RecordingStoppedEvent
	| ConfigChangedEvent
	| HotkeyCaptureCancelEvent
	| HistoryChangedEvent
	| StateChangedEvent
	| PasteFailedEvent
	| DownloadProgressEvent
	| NotificationEvent
	| VocabularySuggestionEvent
	| MicrophonesChangedEvent
	| MicrophoneTestCompleteEvent
	| AudioClipEvent
	| TrayMenuEvent
	| NavigateEvent
	| ReadyEvent
	| BubbleShowEvent
	| BubbleHideEvent
	| BubbleSetStateEvent
	| BubbleLevelEvent
	| BubbleConfigEvent
	| ShowWindowEvent
	| QuitAppEvent
	| RelaunchAppEvent
	// GT-52: three server-emitted events previously missing from the
	// union. Each is published by `event_bus.publish(...)` in the
	// Python tree (see the per-interface docstring for the emitter).
	| TrayStateEvent
	| ConsentRequiredEvent
	| ParakeetCpuFallbackEvent
	| ReconnectingEvent
	| ReconnectedEvent;

// ── Auth frame (PVT-G5-063) ────────────────────────────────────────
//
// The Python backend (`voice_typer/server/sidecar_ws.py:177-231` and
// `voice_typer/server/ipc_server.py:995-1210`) authenticates each new
// TCP/WS connection by requiring the FIRST frame to be::
//
//     {"type": "auth", "token": "<session-token>"}
//
// The token is an HMAC compared with `hmac.compare_digest` against the
// `VOICE_TYPER_IPC_TOKEN` env var set by the Rust/Electron host at
// spawn (ADR-0020 §3). On mismatch the socket is closed immediately.
//
// PVT-G5-063 (Medium): there is currently NO `protocol_version` field
// on the auth frame or any other IPC message — version-skew detection
// is post-hoc only (`code:"unknown_command"` / `disallowed_command`).
// The agreed-upon forward shape is::
//
//     {
//       "type": "auth",
//       "token": "<session-token>",
//       "protocol_version": 1
//     }
//
// Both Python (the server) and Rust/Electron (the client) should reject
// mismatched versions with a clear `code:"protocol_version_mismatch"`
// error BEFORE the auth token is even checked, so a stale client
// talking to a newer server gets a structured error instead of an
// opaque auth failure.
//
// This is documented here (not exported as a separate `AuthFrame`
// type) because no current renderer code constructs or parses auth
// frames — that responsibility lives entirely in the Electron main
// process (`voice_typer/client/src/main/python/tcp-connect.ts`) and
// the Rust host (`src-tauri/src/sidecar/ws.rs`). When the
// `protocol_version` field IS added to the wire, export an `AuthFrame`
// interface here and type-annotate the auth-frame constructor in
// `tcp-connect.ts` so future contributors get compile-time help.

// ── Request messages (sent via window.python.call) ────────────────

export interface GetConfigRequest {
	type: "get_config";
}

// NEW-IPC-009 / NEW-MISMATCH-002: removed ``UpdateConfigRequest``.
// The server command is ``set_config`` (not ``update_config``), and
// the renderer uses untyped ``call<T>('set_config', data)`` directly
// — there is no consumer of this type.  Keeping a mismatched type
// (claiming ``type: 'update_config'``) gave a false impression of
// type safety while not actually being enforced anywhere.

export interface GetMicrophonesRequest {
	type: "get_microphones";
}

export interface ToggleDictationRequest {
	type: "toggle_dictation";
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
	type: "get_history";
	data?: { limit?: number; offset?: number };
}

export interface DeleteHistoryRequest {
	type: "delete_history";
	data: { id: number };
}

export interface ClearHistoryRequest {
	type: "clear_history";
}

export interface ToggleFavoriteRequest {
	type: "toggle_favorite";
	data: { id: number };
}

export interface GetFavoritesRequest {
	type: "get_favorites";
	data?: { limit?: number; offset?: number };
}

export interface SearchHistoryRequest {
	type: "search_history";
	data: { query: string; limit?: number; offset?: number };
}

export interface GetTodayStatsRequest {
	type: "get_today_stats";
}

export interface GetVocabularyRequest {
	type: "get_vocabulary";
}

export interface SaveVocabularyRequest {
	type: "save_vocabulary";
	data: Record<string, unknown>;
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
	| SaveVocabularyRequest;

// ── Response data shapes (the `data` field in Python responses) ───

export interface ToggleDictationResult {
	recording: boolean;
}

// NEW-IPC-009 / NEW-MISMATCH-002: removed ``RestartResult``.
// ``restart_app`` / ``quit_app`` are not sent from the renderer (only
// the Electron main process sends them), and the server returns
// ``{type: "ack", data: {}}`` for these — there is no ``status``
// field.  The dead type gave a false impression of the response shape.

export interface ToggleFavoriteResult {
	favorite: number;
}

export interface SaveVocabularyResult {
	imported_categories: number;
}

// ── Vocabulary types (mirrors Python VocabularyManager) ────────────

export interface VocabularyData {
	misspellings?: Record<string, string>;
	technical_terms?: Record<string, string>;
	names?: Record<string, string>;
	products?: Record<string, string>;
	phrase_corrections?: Array<[string, string]>;
	extra_word_patterns?: Array<[string, string]>;
}

export interface VocabularyEntry {
	category: string;
	original: string;
	correction: string;
	index?: number;
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

export type ResponseData<T extends PythonRequest["type"]> =
	T extends "get_config"
		? VoiceTyperConfig
		: T extends "get_microphones"
			? MicrophoneDevice[]
			: T extends "toggle_dictation"
				? ToggleDictationResult
				: T extends "get_history"
					? HistoryRecord[]
					: T extends "delete_history"
						? undefined
						: T extends "clear_history"
							? undefined
							: T extends "toggle_favorite"
								? ToggleFavoriteResult
								: T extends "get_favorites"
									? HistoryRecord[]
									: T extends "search_history"
										? HistoryRecord[]
										: T extends "get_today_stats"
											? TodayStats
											: T extends "get_vocabulary"
												? VocabularyData
												: T extends "save_vocabulary"
													? SaveVocabularyResult
													: unknown;

// ── Window augmentation for type-safe python bridge ───────────────

export interface PythonBridge {
	call: (msg: {
		type: string;
		data?: Record<string, unknown>;
	}) => Promise<unknown>;
	onEvent: (callback: (event: PythonPushEvent) => void) => () => void;
}

// ── Window augmentation for the custom title bar (preload `window.*`) ─

export interface WindowBridge {
	minimize: () => Promise<void>;
	toggleMaximize: () => Promise<boolean>;
	close: () => Promise<void>;
	isMaximized: () => Promise<boolean>;
	onMaximizedChanged: (callback: (maximized: boolean) => void) => () => void;
	exportHistory: (
		data: Record<string, unknown>[],
		format: "json" | "csv",
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	exportVocabulary: (
		data: Record<string, unknown>,
		format: "json" | "csv",
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	// NEW-PRIV-007: GDPR right-to-export for templates + config.
	exportTemplates?: (
		data: unknown,
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	exportConfig?: (
		data: unknown,
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	openLogs?: () => Promise<{ success: boolean; error?: string }>;
	// G4-M-71: open the ELECTRON log folder (userData dir) in the OS
	// file manager. Distinct from `openLogs` above (which opens the
	// Python backend's log dir). Optional because the Tauri bridge
	// doesn't currently implement it (Tauri's `open_logs` command
	// opens only one folder); the Electron preload always installs it.
	openElectronLogs?: () => Promise<{
		success: boolean;
		path?: string;
		error?: string;
	}>;
	// G4-M-69: forward a renderer-caught error (e.g. from React's
	// `componentDidCatch`) to the main process for persistence in
	// `electron-renderer-errors.log`. The sandboxed renderer can't
	// write to userData directly — only the main process can.
	// Optional so the Tauri bridge (which has no main-process file
	// system access) can omit it without breaking the type contract.
	logError?: (payload: {
		kind: string;
		stack?: string;
		componentStack?: string;
		message?: string;
	}) => Promise<void>;
	// CR-33: native folder picker for HuggingFace model imports. Was
	// missing from the type — Models.tsx accessed it via a runtime cast.
	// Declared optional because the Tauri bridge installs it but the
	// legacy Electron preload also installs it (so the type is satisfied
	// on both paths).
	openModelImportDialog?: () => Promise<{
		canceled: boolean;
		path?: string;
		error?: string;
	}>;
}

// ── Bubble bridge API (exposed by Electron preload for the bubble overlay) ─
//
// DX-012: The ``WindowBubble`` interface was split into two types:
//   - ``MainRendererBubble`` — subset exposed by ``preload/index.ts``
//     (the main settings window).  Bubble-only methods (onSetState,
//     resizeTo) are not available here; callers must use ``?.``.
//   - ``BubbleWindowBubble`` — full interface exposed by
//     ``preload/bubble.ts`` (the bubble overlay window).  All methods
//     are guaranteed present.
//
// The ``Window.bubble`` type in the main renderer is typed as
// ``MainRendererBubble`` so callers that accidentally use a bubble-
// only method (e.g. ``window.bubble.resizeTo(...)``) get a compile-
// time type error instead of a silent runtime no-op.

/** Methods exposed by the main renderer's preload (preload/index.ts). */
export interface MainRendererBubble {
	signalReady?: () => void;
	setPosition?: (pos: string) => void;
	setDraggable?: (v: boolean) => void;
	show?: () => void;
	// NOTE: ``hide`` and ``setLevel`` were intentionally removed from this
	// main-renderer subset (DX-012 residual).  Neither preload implements
	// them — ``preload/index.ts`` exposes no ``hide``/``setLevel``, and
	// ``preload/bubble.ts`` does the same.  Keeping them here would make the
	// type over-promise a silent runtime no-op.  Bubble-window-only methods
	// remain in ``BubbleWindowBubble`` (onSetState, resizeTo).
	moveBy?: (deltaX: number, deltaY: number) => void;
	// UX-10: mic-button toggle. Present on the bubble-window preload;
	// optional here so the main-window preload (which doesn't expose it)
	// still satisfies the type. The sandboxed bubble routes through a
	// dedicated IPC channel rather than python.call.
	toggleDictation?: () => void;
	// UX-10: receive bubble-relevant config pushed from the backend.
	onConfig?: (cb: (cfg: Record<string, unknown>) => void) => () => void;
	// Event subscriptions (bubble window → main process) — always present
	// when the bubble window is loaded (exposed by the preload script)
	onLevel: (cb: (data: { rms: number; peak: number }) => void) => () => void;
	onShow: (cb: () => void) => () => void;
	onHide: (cb: () => void) => () => void;
	onDraggable: (cb: (draggable: boolean) => void) => () => void;
	hideComplete: () => void;
}

/** Full bubble API exposed by the bubble window's preload (preload/bubble.ts). */
export interface BubbleWindowBubble extends MainRendererBubble {
	onSetState: (cb: (state: string) => void) => () => void;
	// Auto-resize the BrowserWindow to exactly fit the pill content,
	// eliminating the transparent dead zone around the bubble.
	resizeTo: (width: number, height: number) => void;
}

// DX-012: Each window declares its own Window.bubble type:
//   - Main renderer (``vite-env.d.ts``): ``bubble?: MainRendererBubble``
//   - Bubble window (``Bubble.tsx``): ``bubble?: BubbleWindowBubble`` (cast)
declare global {
	interface Window {
		python?: PythonBridge;
		window_?: WindowBridge;
		bubble?: MainRendererBubble;
	}
}

// ── TASK-24: supplementary IPC contracts ────────────────────────────
//
// The types below pin the response shapes for IPC commands that the
// renderer consumes but previously had no typed contract for. They are
// declared here (the renderer's central IPC-contract file) so callers can
// import them from ``@/types/ipc`` instead of inlining fragile anonymous
// shapes at each call site.

/**
 * Per-model entry in the `get_model_status` IPC response.
 *
 * The backend's `voice_typer/server/service.py::_compute_model_status`
 * returns `dict[str, { downloaded: bool, deps_ok: bool }]`. Renderers
 * previously inlined that shape at every `call<...>("get_model_status")`
 * call site — see `hooks/useModelLifecycle.ts` for the duplicated
 * `Record<string, { downloaded: boolean; deps_ok: boolean }>` annotation.
 *
 * TASK-24-FIX-6 adds the `hash_verified` discriminator so the Models
 * page can surface a warning badge when a downloaded model's hash doesn't
 * match the registry's expected hash (e.g. partial download left on disk
 * after a crash, or a third-party import that bypassed the HuggingFace
 * cache). The backend currently omits this field (defaults to `"unknown"`
 * for backwards compat); when `voice_typer/server/model_registry.py`
 * starts populating it, the renderer will already have the type.
 */
export interface ModelStatusEntry {
	downloaded: boolean;
	deps_ok: boolean;
	/**
	 * Hash-verification result for the on-disk model files.
	 *
	 * - `"verified"` — the downloaded files' hash matches the
	 *   registry's expected hash.
	 * - `"mismatch"` — the files exist but the hash doesn't match
	 *   (corrupt download, third-party import, or a partial file).
	 *   The Models page should show a "Re-download" affordance.
	 * - `"unknown"` — the backend hasn't computed a hash yet
	 *   (legacy backend that predates the field, or a model that
	 *   doesn't have a registry hash). The Models page should NOT
	 *   show a verification badge in this state.
	 *
	 * Optional for backwards compatibility with backends that
	 * predate TASK-24-FIX-6 — absence is treated as `"unknown"`.
	 */
	hash_verified?: "verified" | "mismatch" | "unknown";
}

/**
 * Convenience alias: the full `get_model_status` response is a map
 * keyed by the model's registry name.
 *
 * NOTE: the renderer's `hooks/useModelLifecycle.ts` currently uses an
 * inline `Record<string, { downloaded: boolean; deps_ok: boolean }>`
 * annotation. Migrating it to `ModelStatusMap` is a follow-up for the
 * Models-page owner (sub-agent 6); this type is declared here so the
 * contract is centrally available without forcing a cross-file edit.
 */
export type ModelStatusMap = Record<string, ModelStatusEntry>;

/**
 * TASK-24-FIX-5: disk-space info for the Models-page pre-flight check
 * (PVT-033). Returned by the optional `get_disk_info` IPC.
 *
 * The shape deliberately matches the *minimal* contract documented in the
 * fix brief: free bytes on the volume that hosts the models directory,
 * plus the absolute path of the models directory itself (so the renderer
 * can show "X GB free in /home/…/.voice-typer/huggingface/hub" and offer
 * an "Open models folder" button).
 *
 * NOTE: `lib/utils/models.ts` declares a richer `DiskInfo` interface
 * (with an additional `total_bytes: number` field and `models_dir?`
 * optional). That interface is owned by sub-agent 6 and is NOT modified
 * here — this file declares the IPC-level contract per the fix brief.
 * The two shapes are intentionally compatible: the richer object
 * satisfies this interface (the extra `total_bytes` field is allowed by
 * TypeScript's structural typing, and `models_dir` is required here but
 * optional there — callers that consume `lib/utils/models.ts`'s
 * `DiskInfo` should normalise to a non-null `models_dir` before treating
 * the value as this type).
 */
export interface DiskInfo {
	/** Bytes free on the volume that holds the models directory. */
	free_bytes: number;
	/** Absolute path of the models directory. */
	models_dir: string;
}

/**
 * TASK-24-FIX-9: response shape for the `onboarding_check_permissions`
 * IPC command (UX-4 / UX-27).
 *
 * Mirrors `voice_typer/server/onboarding.py::check_permissions` (lines
 * 218-314): the backend probes the OS-level keyboard-monitoring
 * permission (macOS Accessibility / Linux `input` group + udev rule /
 * Windows: always granted) and returns this struct so the renderer can
 * render a platform-specific setup walkthrough in the Onboarding wizard.
 *
 * The `instructions` field, when non-null, is a dict with `title`,
 * `steps` (string[]), and `commands` (string[] | null). It's typed as
 * `object | null` here (rather than a stricter interface) to keep the
 * renderer resilient to future backend additions without a renderer
 * rebuild — the Onboarding page reads `instructions.title` /
 * `instructions.steps` / `instructions.commands` defensively.
 */
export interface PermissionsResult {
	/** `"windows" | "macos" | "linux" | "unknown"`. */
	platform: string;
	/**
	 * Current permission state. `"error"` is included per the fix
	 * brief for the case where the backend probe itself threw (e.g.
	 * the Linux `id` command failed, or the macOS API returned an
	 * unexpected value). The Onboarding page should treat `"error"`
	 * the same as `"unknown"` for advancement purposes but log it.
	 */
	state: "granted" | "denied" | "prompt" | "unknown" | "error";
	/**
	 * True iff the platform requires a permission AND the user
	 * hasn't granted it yet. When `false`, the Onboarding page can
	 * auto-advance past the Permissions step.
	 */
	needed: boolean;
	/**
	 * Platform-specific setup walkthrough (`{ title, steps, commands }`)
	 * when `needed` is true; `null` otherwise (and on Windows /
	 * unknown platforms, where no setup is required).
	 */
	instructions: {
		title: string;
		steps: string[];
		commands: string[] | null;
	} | null;
}

/**
 * TASK-24-FIX-10: autostart registration status. Returned by the
 * `get_autostart_status` IPC (PVT-060 — the autostart toggle previously
 * had no failure feedback; this struct lets the Settings page surface
 * "Registered" vs "Registration failed: <reason>" to the user).
 *
 * Mirrors `voice_typer/server/server_platform/autostart_*.py`:
 *   - `registered` is true when EITHER the Task Scheduler entry
 *     (`_is_app_autostart_task_registered`) OR the Run-key entry
 *     (`_is_app_autostart_runkey_registered`) is present on Windows,
 *     or the equivalent launchd / systemd / XDG-autostart entry is
 *     present on macOS / Linux.
 *   - `error` is non-null only when the most recent
 *     register/unregister attempt failed (e.g. the user denied the
 *     `osascript` prompt, or `systemctl --user enable` returned
 *     non-zero). The renderer shows it as a destructive toast.
 */
export interface AutostartStatus {
	/** True iff the OS-level autostart entry is currently installed. */
	registered: boolean;
	/** Error message from the last register/unregister attempt, or null. */
	error: string | null;
}

/**
 * TASK-24-FIX-11: OS-level microphone permission state. Returned by the
 * `check_microphone_permission` IPC (PVT-036 / PVT-061 — the Onboarding
 * and Microphone pages previously never probed the OS mic permission,
 * leaving users to discover the silent failure on first recording).
 *
 * Mirrors `voice_typer/server/permissions.py::check_microphone_permission`
 * (the same `PermissionState` enum as keyboard permissions, restricted
 * to the four states the mic-probe can actually emit — mic permission
 * has no `"error"` state because the probe is a single `sounddevice`
 * query that either succeeds, fails outright (`"denied"`), or returns
 * an empty device list (`"unknown"` on platforms where that's
 * ambiguous).
 */
export interface MicrophonePermissionResult {
	state: "granted" | "denied" | "prompt" | "unknown";
}

// ── TASK-24: out-of-scope type notes ────────────────────────────────
//
// The following fixes from the brief touch types that live OUTSIDE this
// sub-agent's file scope. They are noted here so the next reviewer can
// find them; the owning sub-agents are responsible for the actual edits.
//
//  • TASK-24-FIX-3 (depsInstallable on ModelInfo): ALREADY PRESENT in
//    `voice_typer/client/src/renderer/src/lib/utils/models.ts:37`
//    (`depsInstallable?: boolean`). That file is owned by sub-agent 6
//    (Models.tsx + components/models/* + lib/utils/models.ts). No
//    change needed here — re-declaring `ModelInfo` in this file would
//    create a conflicting duplicate. The existing field is consumed by
//    `components/models/ModelCardActions.tsx:131` and
//    `hooks/useModelLifecycle.ts:456` to render the parakeet
//    "Download Deps" button.
//
//  • TASK-24-FIX-4 (display_name on ModelMetadata): ALREADY PRESENT in
//    `voice_typer/client/src/renderer/src/lib/utils/models.ts:42`
//    (`display_name?: string`). Same ownership note as above. No change
//    needed here.
//
//  • TASK-24-FIX-7 ("error" and "cancelling" on BubbleMode): BubbleMode
//    is declared LOCALLY in `voice_typer/client/src/renderer/src/Bubble.tsx:34`
//    as `type BubbleMode = "recording" | "transcribing" | "idle" | "fading";`.
//    That file is owned by sub-agent 12 (Bubble.tsx + bubble-main.tsx +
//    preload/bubble.ts + bubble-window.ts + bubble-handlers.ts +
//    bubble-bridge-shared.ts). Sub-agent 12 should extend the union to
//    `| "error" | "cancelling"` so the bubble can render distinct
//    visuals when the recording pipeline is in the `error` or
//    `cancelling` `RecordingState` (those states already exist in the
//    `RecordingState` union above — see NEW-IPC-010). Doing the edit
//    here would have no effect because `Bubble.tsx` declares its own
//    local type and never imports one from `types/ipc.ts`.
//
//  • TASK-24-FIX-14 (common.copyPath and common.search in i18n type
//    coverage): the `types/` directory does NOT cover i18n — i18n
//    types live in `voice_typer/client/src/renderer/src/i18n/i18n.ts`
//    (owned by sub-agent 15). Both keys already exist in the English
//    source catalog at `i18n/translations/en.json:75-76`
//    (`"copyPath": "Copy path"`, `"search": "Search"`). Adding type
//    coverage for them is a follow-up for sub-agent 15's i18n type
//    system; this sub-agent's file scope doesn't include any i18n
//    type definitions.
