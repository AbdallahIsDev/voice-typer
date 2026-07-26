// types/ipc/bubble_bridge.ts
//
// The bubble-window bridge interfaces + the `declare global { Window }`
// augmentation that exposes `window.python` / `window.window_` /
// `window.bubble` to renderer code.
//
// Split out from the original monolithic `types/ipc.ts` (DT-31 / DT-FIX-7).
// No behaviour change vs. the original file — pure structural refactor.
//
// TypeScript merges `declare global { interface Window { ... } }` blocks
// across files, so colocating the augmentation here (rather than in a
// dedicated `global.d.ts`) is sound — every renderer file that imports
// any type from `@/types/ipc/*` triggers this module's evaluation and
// the augmentation takes effect project-wide.
//
// Imports `PythonBridge` + `WindowBridge` from `./bridge` for the
// global Window augmentation.

import type { PythonBridge, WindowBridge } from "./bridge";

// ── Bubble bridge API (exposed by Electron preload for the bubble overlay) ─
//
// DX-012: The ``WindowBubble`` interface was split into three
// composable types so the main renderer's `window.bubble` (typed as
// ``MainRendererBubbleMutators`` only) gets a compile-time error if it
// tries to call bubble-only methods OR subscribe to bubble-only events:
//   - ``MainRendererBubbleMutators`` — the mutator subset exposed by
//     ``preload/index.ts`` (the main settings window).  Bubble-only
//     mutators (onSetState, resizeTo, toggleDictation, onConfig,
//     hideComplete) and ALL event subscriptions are NOT available
//     here; callers must use ``?.``.
//   - ``BubbleEventSubscriptions`` — the event-subscription subset
//     (``onLevel`` / ``onShow`` / ``onHide`` / ``onDraggable``).
//     Separated from mutators so the Tauri bridge installer can skip
//     wiring these on the main renderer (the main renderer has no
//     reason to subscribe to bubble-window lifecycle events — that
//     was a leaky abstraction that installed dead listeners on main
//     and silently no-op'd when the events never arrived).
//   - ``BubbleWindowExtras`` — bubble-window-only mutators
//     (onSetState, resizeTo, toggleDictation, onConfig, hideComplete).
//   - ``BubbleWindowBubble`` — the full interface exposed by
//     ``preload/bubble.ts`` (the bubble overlay window).
//     ``MainRendererBubbleMutators & BubbleEventSubscriptions & BubbleWindowExtras``.
//     All methods are guaranteed present.
//
// ``hideComplete`` was moved from the main-renderer subset to
// ``BubbleWindowExtras`` — only the bubble renderer's exit-animation
// handler should invoke it, and exposing it on the main renderer was
// a leaky abstraction (no main-renderer caller exists). The Electron
// preload's exposure of `hideComplete` on main was removed in the same
// fix.
//
// The ``Window.bubble`` type in the main renderer is typed as
// ``MainRendererBubbleMutators`` so callers that accidentally use a
// bubble-only method (e.g. ``window.bubble.resizeTo(...)``) get a
// compile-time type error instead of a silent runtime no-op.

/**
 * Mutator methods exposed by BOTH the main renderer's preload
 * (`preload/index.ts`) AND the bubble window's preload
 * (`preload/bubble.ts`). These are the only bubble methods the main
 * renderer legitimately calls — `show`, `setPosition`, `setDraggable`,
 * `moveBy`, `signalReady`.
 *
 * `hideComplete` is NOT here — it's a bubble-window-only mutator
 * (see `BubbleWindowExtras`). The main renderer has no reason to call
 * "hide-complete" because the main renderer doesn't own the bubble's
 * exit-animation lifecycle.
 *
 * All fields are optional because the Electron preload exposes them
 * conditionally (some are Tauri-only and don't exist under Electron).
 * The Tauri bridge in `tauri-bridge/bubble-namespace.ts` always
 * installs them on both windows.
 */
export interface MainRendererBubbleMutators {
	signalReady?: () => void;
	setPosition?: (pos: string) => void;
	setDraggable?: (v: boolean) => void;
	show?: () => void;
	// NOTE: ``hide`` and ``setLevel`` were intentionally removed from this
	// main-renderer subset (DX-012 residual).  Neither preload implements
	// them — ``preload/index.ts`` exposes no ``hide``/``setLevel``, and
	// ``preload/bubble.ts`` does the same.  Keeping them here would make the
	// type over-promise a silent runtime no-op.  Bubble-window-only methods
	// remain in ``BubbleWindowExtras`` (onSetState, resizeTo, hideComplete).
	moveBy?: (deltaX: number, deltaY: number) => void;
}

/**
 * Event-subscription methods for bubble lifecycle events.
 * Separated from `MainRendererBubbleMutators` so the Tauri bridge
 * installer can skip wiring these on the main renderer (where they'd
 * install dead listeners — the main renderer has no reason to listen
 * to bubble-window events).
 *
 * The bubble window's preload (`preload/bubble.ts`) always installs
 * these — they're required (non-optional) on the bubble window.
 *
 * The fields are required (not optional) because the bubble renderer's
 * components (Bubble.tsx) call them without `?.` — the type system
 * enforces that the bubble preload always provides them.
 */
export interface BubbleEventSubscriptions {
	// Event subscriptions (bubble window → main process) — always present
	// when the bubble window is loaded (exposed by the preload script)
	onLevel: (cb: (data: { rms: number; peak: number }) => void) => () => void;
	onShow: (cb: () => void) => () => void;
	onHide: (cb: () => void) => () => void;
	onDraggable: (cb: (draggable: boolean) => void) => () => void;
}

/**
 * Mutator methods exposed ONLY by the bubble window's
 * preload (`preload/bubble.ts`). The main renderer's preload does NOT
 * expose these — they're the bubble-window-only extensions that mirror
 * the Electron preload's split.
 *
 * - `onSetState` / `resizeTo` / `toggleDictation` / `onConfig` were
 *   already bubble-only in the prior `BubbleWindowBubble` type
 *   (they `extends MainRendererBubble`); the split just makes it
 *   explicit.
 * - `hideComplete` was moved here from `MainRendererBubble`
 *   because only the bubble renderer's exit-animation handler should
 *   invoke it.
 */
export interface BubbleWindowExtras {
	// UX-10: mic-button toggle. Present on the bubble-window preload;
	// the sandboxed bubble routes through a dedicated IPC channel
	// rather than python.call.
	toggleDictation: () => void;
	// UX-10: receive bubble-relevant config pushed from the backend.
	onConfig: (cb: (cfg: Record<string, unknown>) => void) => () => void;
	// CR-33: bubble renderer listens for `bubble:set-state` events
	// pushed by the Rust WS reader task (see sidecar/ws.rs
	// `translate_event_name`).
	onSetState: (cb: (state: string) => void) => () => void;
	// Auto-resize the BrowserWindow to exactly fit the pill content,
	// eliminating the transparent dead zone around the bubble.
	resizeTo: (width: number, height: number) => void;
	// notify the host that the bubble's exit animation has
	// finished and the window can be hidden. Only the bubble
	// renderer's exit-animation handler should invoke this — the
	// main renderer has no equivalent lifecycle.
	hideComplete: () => void;
}

/**
 * Full bubble API exposed by the bubble window's preload
 * (preload/bubble.ts). Composed from `MainRendererBubbleMutators`
 * (shared mutators) + `BubbleEventSubscriptions` (bubble-only event
 * subscriptions) + `BubbleWindowExtras` (bubble-only mutators).
 *
 * All fields from `MainRendererBubbleMutators` remain optional (the
 * Electron preload may not install all of them under all configs).
 * Fields from `BubbleEventSubscriptions` and `BubbleWindowExtras` are
 * required (the bubble renderer's components rely on them).
 */
export type BubbleWindowBubble = MainRendererBubbleMutators &
	BubbleEventSubscriptions &
	BubbleWindowExtras;

/**
 * Backwards-compat alias. The prior `MainRendererBubble` type
 * included both mutators AND event subscriptions. The main renderer
 * now uses `MainRendererBubbleMutators` only (the event subscriptions
 * are a leaky abstraction on main). Callers that still
 * reference `MainRendererBubble` get the new narrower type via this
 * alias so the migration is opt-in.
 *
 * TODO: remove this alias once all call sites have migrated to
 * `MainRendererBubbleMutators`.
 */
export type MainRendererBubble = MainRendererBubbleMutators;

// DX-012: Each window declares its own Window.bubble type:
//   - Main renderer (``vite-env.d.ts``): ``bubble?: MainRendererBubbleMutators``
//     (mutators only — no event subscriptions, no bubble-only extras)
//   - Bubble window (``Bubble.tsx``): ``bubble?: BubbleWindowBubble`` (cast)
//
// `python` and `window_` are exposed by the Electron preload
// (`preload/index.ts`) and the Tauri bridge installer
// (`tauri-bridge/python-namespace.ts` / `tauri-bridge/window-namespace.ts`).
// They're optional because some test harnesses and SSR-like render
// paths construct `window` without the preload having run.
declare global {
	interface Window {
		python?: PythonBridge;
		window_?: WindowBridge;
		bubble?: MainRendererBubbleMutators;
	}
}
