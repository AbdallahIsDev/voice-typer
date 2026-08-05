// types/ipc/push_events.ts
//
// All Python-push-event interfaces + the `PythonPushEvent` discriminated
// union.
//
//Split out from the original monolithic `types/ipc.ts` ( / ).
// No behaviour change vs. the original file — pure structural refactor.
//
// Imports `ErrorCodes` from `./enums.ts` for the `ErrorEvent` payload.

import type { ErrorCodes } from "./enums";

// ── Push events from Python (no id field) ─────────────────────────

export interface StatusChangeEvent {
	type: "status_change";
	data: { status: string };
}

//(part 4): ErrorEvent now mirrors the actual Python error
// envelope. The server emits one of these shapes via `_send_error(...)`:
//   - {type:"error", data:{code:"unknown_command", message, command?}}
//   - {type:"error", data:{code:"invalid_field"|"missing_field", message, field?}}
//   - {type:"error", data:{code:"unknown_tray_item", id?}} (no `message`)
//   - {type:"error", data:{code:"internal_error"|"rate_limited", message}}
// `code` is always present (REQUIRED); `message` is conventionally present
//but omitted for `unknown_tray_item` (: made OPTIONAL on the TS
// side to match the emitter reality). The 4 optional fields cover all
// variants without forcing callers to narrow on `code` first.
//
// Note: this is the module-local ErrorEvent (Python IPC). It does NOT
// collide with the global DOM `ErrorEvent` used by `addEventListener`
// in `globalErrorHandler.ts` — that file does not import this type and
// resolves `ErrorEvent` to the lib.dom.d.ts declaration.
export interface ErrorEvent {
	type: "error";
	data: {
		code: ErrorCodes;
		//`message` is OPTIONAL — the `unknown_tray_item`
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

//(part 3): the dead `TranscriptionPartialEvent` type was
// REMOVED. No `event_bus.publish({"type": "transcription_partial"})`
// exists anywhere in the Python tree — the partial-transcription path
// in `dictation_pipeline.py` only publishes `transcription_final`. The
// old type was a phantom contract that gave a false impression of an
// IPC event that never fires. Do NOT re-add it without also wiring up
// a publisher (`event_bus.publish({"type":"transcription_partial", ...})`).
// The compile-time guard in `types/__tests__/ipc-types.test.ts` will
// fail tsc if this is re-added (the union length assertion drops by 1).

//(part 1): `transcription_final` payload nests inside `data`
// (matching `voice_typer/server/dictation_pipeline.py:1331`), NOT at the
// root. The old shape declared `text: string` at the root — a type lie.
// Runtime reader `Home.tsx:428` already accesses `data?.text`, so this
// fix aligns the type with the wire format AND the existing consumer.
//
//the optional `duration_ms?: number` field was REMOVED —
// verified the Python emitter (`event_bus.publish({"type":
// "transcription_final", "data": {"text": text[:200]}})`) never
// populates it. Keeping an optional-but-never-sent field gave a false
// impression of an IPC contract that doesn't exist; any renderer code
// reading `event.data.duration_ms` would always get `undefined` at
// runtime. Do NOT re-add this field without ALSO wiring up the
// Python emitter to populate it (and a parity test in
// `types/__tests__/ipc-types.test.ts`).
export interface TranscriptionFinalEvent {
	type: "transcription_final";
	data: { text: string };
}

//(part 2): the Python emitters for `recording_started` and
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

//(d-review): the dead `ModelLoadedEvent` type was REMOVED.
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

//Additional Python push events ( / ) ───────
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

//emitted on every client connect
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

//the 18 most important missing event types. Each one is
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
 *   (addresses []): the wire event was renamed from
 *  `relaunch_electron` to `relaunch_app` on the Python+Tauri sides in
 *   The renderer type now models `relaunch_app` as the canonical
 *  event.
 *
 *  : the legacy `RelaunchElectronEvent` (type: "relaunch_electron")
 *  was DELETED — verified the Python side emits ONLY `relaunch_app` now
 *  (the `relaunch_electron` symbol survives only in historical comments
 *  in `voice_typer/server/app.py` and `voice_typer/server/ipc_server.py`,
 *  not as a wire event). The transition window for old sidecars has long
 *  since closed. */
export interface RelaunchAppEvent {
	type: "relaunch_app";
	data: Record<string, unknown>;
}

//server-emitted push events previously missing from the union ─
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

// ── 3 server-emitted push events previously missing from the union ─
//
// The Python backend emits these via `event_bus.publish(...)` but the TS
// `PythonPushEvent` union never modelled them, so renderer code
// subscribing via `usePythonEvent("asr_backend_disabled", ...)` etc. got
// no compile-time type narrowing (the events flowed through
// `handleMessage`'s catch-all `broadcastToMainWindow("python-event", msg)`
// path but were typed as `never` in the union, forcing
// `as unknown as PythonPushEvent` casts — a Rule 26 violation).
//
// WIRE-SHAPE NOTE (corrected): the Python emitters for
// `asr_backend_disabled` and `asr_last_resort_unloaded` put the
// payload fields under the canonical `data:` key — matching every
// other `event_bus.publish(...)` caller in the codebase. Verified
// by reading the emitters at:
//   - `voice_typer/server/asr_registry.py:625-637` (`asr_backend_disabled`)
//   - `voice_typer/server/asr_registry.py:361-372` (`asr_last_resort_unloaded`)
//   - `voice_typer/server/dictation_pipeline.py:919` (`llm_polish_failed`)
// The TS interfaces below mirror the actual wire shape (nested under
// `data:`). The earlier comment block claimed the fields were at the
// message ROOT — that was a stale claim from before the Python
// emitters were wrapped in the `data:` envelope; the parity test
// in `types/__tests__/ipc-types.test.ts` was extended to
// assert the corrected shape so a future regression here is surfaced.

/** Pushed by `voice_typer/server/asr_registry.py:625-637` when an ASR
 *  backend (e.g. whisper CUDA) auto-disables after repeated OOM / load
 *  failures and the registry falls back to a different backend. The
 *  renderer surfaces a one-time "ASR backend X disabled, falling back
 *  to Y" banner so the user knows transcription may be slower or use
 *  a different model.
 *
 *  Wire shape (payload nested under `data:` — see the note above):
 *    `{ "type": "asr_backend_disabled", "data": {
 *        "backend": "<name>", "failure_count": <int>,
 *        "timestamp": "<iso-8601>" } }` */
export interface ASRBackendDisabledEvent {
	type: "asr_backend_disabled";
	data: {
		/** The disabled backend's name (e.g. `"whisper"`, `"parakeet"`). */
		backend: string;
		/** Number of consecutive failures that triggered the disable. */
		failure_count: number;
		/** ISO-8601 timestamp emitted by the Python `datetime.now(timezone.utc)`. */
		timestamp: string;
	};
}

/** Pushed by `voice_typer/server/asr_registry.py:361-372` when the
 *  LAST-RESORT ASR backend is unloaded — i.e. no ASR backend is
 *  available until the user manually restarts the app or reconfigures.
 *  The renderer surfaces a critical "No ASR backend available — please
 *  restart" banner so the user knows dictation is unavailable.
 *
 *  Wire shape (payload nested under `data:` — see the note above):
 *    `{ "type": "asr_last_resort_unloaded", "data": {
 *        "backend": "<name>", "timestamp": "<iso-8601>" } }` */
export interface ASRLastResortUnloadedEvent {
	type: "asr_last_resort_unloaded";
	data: {
		/** The last-resort backend's name that was just unloaded. */
		backend: string;
		/** ISO-8601 timestamp emitted by the Python `datetime.now(timezone.utc)`. */
		timestamp: string;
	};
}

/** Pushed by `voice_typer/server/dictation_pipeline.py:919` when the
 *  LLM polish step (the optional `ai_enhancement_enabled` path that
 *  post-processes the raw transcription with grammar / style fixes)
 *  raises an exception. The transcription itself is still delivered
 *  to the user UN-polished (the `dictation_pipeline` swallows the
 *  error and returns the original text), so this event is purely
 *  informational — the renderer may surface a one-time toast like
 *  "Polish unavailable — transcription shown raw".
 *
 *  Wire shape: the Python emitter publishes a bare
 *  `{ "type": "llm_polish_failed" }` frame with NO payload fields.
 *  Mirrors the shape of {@link RecordingStartedEvent} /
 *  {@link HotkeyCaptureCancelEvent} (bare `{type}` frames with no
 *  data). */
export interface LLMPolishFailedEvent {
	type: "llm_polish_failed";
}

// ── (resilient sidecar) lifecycle events ──────────────────────
//
//(addresses []): these events are NOT emitted by the Python
// backend — they are synthesized by the host bridge (Tauri Rust
// `src-tauri/src/sidecar/supervisor.rs` or Electron main) when the transport
// layer detects a disconnect and enters the reconnect loop. They
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

//a coalesced microphone-level push event published by
// `voice_typer/server/level_monitor.py` via the same bounded-queue +
// worker pattern as `bubble_level` (≤30 Hz). Consumed by
// `pages/microphone/hooks/useMicrophoneTest.ts` instead of the legacy
// 10 Hz `microphone_test_get_level` IPC poll. The payload mirrors the
// dict returned by the legacy IPC handler so the renderer's
// `setLevel` / `setPeak` / `setMicMonitoring` setState calls work
// unchanged.
export interface MicLevelEvent {
	type: "mic_level";
	data: { level: number; peak: number; active: boolean };
}

// NOTE: `usePythonEvent`'s `type` param IS narrowed to
// `PythonPushEvent["type"]` via a two-overload signature in
// `hooks/usePython.ts`. The first overload
// (`<K extends PythonPushEvent["type"]>(type: K, ...)`) catches typos
// like `usePythonEvent("past_failed", ...)` at compile time (the literal
// `"past_failed"` is not in the union). The second overload accepts any
// `string` for forward-compat with backend-added events not yet in the
// union; a dev-time `KNOWN_EVENT_TYPES` warning (locked in by
// `hooks/__tests__/usePython-known-event-types-parity.test.ts`) surfaces
// unknown literals in development so contributors don't silently fall
// through to overload 2 with a typo. This union is the single source of
// truth for the compile-time narrowing.
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
	//three server-emitted events previously missing from the
	// union. Each is published by `event_bus.publish(...)` in the
	// Python tree (see the per-interface docstring for the emitter).
	| TrayStateEvent
	| ConsentRequiredEvent
	| ParakeetCpuFallbackEvent
	// three more server-emitted events previously missing from
	// the union. Each is published by `event_bus.publish(...)` in the
	// Python tree:
	//   - `asr_backend_disabled`     — `asr_registry.py:625-637`
	//   - `asr_last_resort_unloaded` — `asr_registry.py:361-372`
	//   - `llm_polish_failed`        — `dictation_pipeline.py:919`
	// See the per-interface docstrings for the wire shape (all three
	// payloads nest under `data:` — the earlier "ROOT fields"
	// comment was a stale claim from before the Python
	// emitters were wrapped in the canonical `data:` envelope).
	| ASRBackendDisabledEvent
	| ASRLastResortUnloadedEvent
	| LLMPolishFailedEvent
	| ReconnectingEvent
	| ReconnectedEvent
	//coalesced mic-level push event (≤30 Hz).
	// See `MicLevelEvent` above for the wire shape + emitter.
	| MicLevelEvent;

//Auth frame () ────────────────────────────────────────
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
// the auth frame now carries an OPTIONAL
// `protocol_version` integer. The Python TCP receiver
// (`voice_typer/server/ipc/transport_tcp.py`) and the Rust host
// (`src-tauri/src/sidecar/ws.rs`) both pin the current constant
// below; a mismatch is rejected BEFORE the token check with a
// structured `server.protocol_version_mismatch` error envelope so a
// stale client gets a clear error instead of an opaque `auth_failed`.
// Bump on any wire-incompatible change to the auth frame shape or
// any command's request/response schema.
//
// Cross-language parity: the same integer is defined in:
//   - Python: `voice_typer/server/ipc/transport_tcp.py:IPC_PROTOCOL_VERSION`
//   - Python: `voice_typer/server/sidecar_ws.py:PROTOCOL_VERSION`
//   - Rust:   `src-tauri/src/sidecar/ws.rs:EXPECTED_PROTOCOL_VERSION`
//   - TS:     `IPC_PROTOCOL_VERSION` (this file)
// The cross-language parity test in
// `tests/test_ipc_protocol_cross_language_parity.py` asserts they all agree.
export const IPC_PROTOCOL_VERSION = 1;

// The auth frame shape on the wire. The Rust host constructs this
// frame (`src-tauri/src/sidecar/ws.rs:queue_auth_and_store_ws_tx`);
// the Python TCP / WS receivers parse and validate it
// (`voice_typer/server/ipc/transport_tcp.py:_handle_tcp_connection`
// and `voice_typer/server/sidecar_ws.py:_authenticate`).
//
// `protocol_version` is OPTIONAL: legacy senders that omit it
// continue to function (the receiver's validate-if-present check
// skips to the token check). New senders SHOULD include the field so
// version skew surfaces at handshake time with a structured error.
export interface AuthFrame {
	type: "auth";
	token: string;
	protocol_version?: number;
}

// Error envelope emitted on the version-mismatch path. Emitted by
// `voice_typer/server/ipc/transport_tcp.py:_handle_tcp_connection`
// when an inbound auth frame carries an explicit `protocol_version`
// that does not match `IPC_PROTOCOL_VERSION`. The check runs BEFORE
// the token check so a stale client gets a structured rejection
// instead of an opaque `auth_failed`.
//
// Note: `code` is a string literal here (NOT the `ErrorCodes` union
// from `./enums.ts`) because the renderer does not branch on this
// specific code today — it surfaces as a generic auth-failure toast.
// Adding it to the `ErrorCodes` union in `./enums.ts` is tracked as
// a separate cross-language parity task (the file is owned by another
// sub-agent's slice).
export interface ProtocolVersionMismatchError {
	type: "error";
	data: {
		code: "server.protocol_version_mismatch";
		message: string;
		client_protocol_version: number;
		server_protocol_version: number;
	};
}
