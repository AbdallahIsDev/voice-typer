# Renderer code notes (relocated comment essays)

Deep rationale removed from `voice_typer/client/src/**/*.{ts,tsx}` during comment-bloat cleanup. Anchors (`SEC-*`, `C-*`, `RACE-*`, `PERF-*`) stay in source as short pointers.

## push_events

IPC push-event types were split from monolithic `types/ipc.ts`. Non-obvious contracts kept in source (1–3 lines):

- `ErrorEvent.message` optional — `unknown_tray_item` emitter omits it; module-local type ≠ DOM `ErrorEvent`.
- `TranscriptionPartialEvent` live streaming partial; `supported?: false` is a one-shot capability-gap frame.
- `TranscriptionFinalEvent` payload nests under `data`; `duration_ms` intentionally absent (emitter never sends it).
- `RecordingStarted/Stopped` are bare `{type}` frames (no timestamp/duration on the wire).
- `ConsentRequiredEvent` fields all optional — no single emitter sends all; consumer reads `consent_field`.
- Union must include every `event_bus.publish` type so typos fail compile.

## microphone level monitor

`useMicrophoneLevelMonitor` owns level/peak state + `level_monitor_*` IPC + `mic_level` push (replaces 10 Hz poll). Perf: ref+rAF imperative fill write (mirrors bubble `useAudioLevels`); React state throttled ~8 Hz for text/aria consumers only. Module-scoped start/stop seq counters survive remount races. Start retry backoff for cold-start bridge races. `C-BG-1`: no monitor while window hidden / non-Microphone route.

## sound-manager

Singleton audio cues (`start`/`stop`/`error` only — `C-SOUND-1`). AudioContext: flag set only after successful construction; gesture listener resumes autoplay-locked context; HTMLAudioElement fallback. `setEnabled` syncs Settings + config load into localStorage mirror. Handler stored on module scope so test reset can detach listeners.

## useConnection

Zustand-backed connection/recording state. Lifecycle effects: initial probe, health check (skips when recent push events prove liveness), background reconnect, respawn-exhausted handling. Status pill + description share one `{recordingState, lastError}` write path (`C-HOME-1` via `applyStatusWithReason`).

## format.ts

Locale-aware `Intl` formatters (bytes, numbers, duration, relative time). Module-level formatter caches keyed by locale+options (constructors are expensive). `formatDuration` uses `t()` with fallback keys; 0 renders bare `"0"`. `compactNumber` keeps `K`/`K+` for Dashboard callers.

## useModelDownload

Models-page download progress slice. Single `useState<DownloadState>` + patch on each `download_progress` event (was 9 setters). Failure keeps in-place bar + Retry; cancel can target a queued model by name.

## tauri-bridge

Public entry: named exports only — install side effect lives in `install.ts` (`main.tsx` / `bubble-main.tsx` import it). `window.python.call` / `onEvent` contract is identical on Tauri and predecessor paths. Error-envelope checks in `usePython` are predecessor-only in practice (Tauri rejects on error before JS sees data).

## bubble-namespace / SEC-026

`window.bubble` mutators fire-and-forget to Rust. Main renderer gets only the 5 shared mutators; bubble-window methods (`onConfig`, `resizeTo`, `toggleDictation`, `hideComplete`, event subscriptions) install only when `windowLabel === "bubble"`. Unknown labels default to the smaller main subset.

## i18n store / C-BRAND-1

Locale values use `{appName}`; substitution runs once per locale from `APP_NAME`. Never hardcode brand strings in translations.

## theme / focus / sidebar

Pointer-modality focus (`C-FOCUS-3`) lives in `SearchField` / `usePointerFocusModality` — text inputs always match `:focus-visible` on click. Full-opacity `ring-ring` + `ring-1` (`C-FOCUS-2/5`). Sidebar contracts: nav-only (`C-SIDEBAR-1`), one theme switch in TitleBar (`C-SIDEBAR-2/3`), no remount on collapse, two-group hierarchy (`C-SIDEBAR-20`).
