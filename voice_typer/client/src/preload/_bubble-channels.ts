// Shared bubble-window IPC channel factory.
//
// `preload/bubble.ts` (sandboxed bubble-window preload, SEC-026) and
// `preload/index.ts` (main-renderer preload) previously each declared
// the same ~9 bubble-channel handlers verbatim — a copy-paste
// maintenance hazard where a channel rename or signature fix had to be
// applied in two places. This module is the single source of truth for
// the bubble-channel wiring; the two preload files call the factory
// with `includeRestricted: true` (bubble window gets the full surface)
// or `includeRestricted: false` (main renderer gets only the shared
// subset — `onSetState` / `onConfig` / `hideComplete` / `resizeTo` /
// `toggleDictation` / `dismiss` are bubble-window-only).
//
// The factory takes `ipc` (the Electron `ipcRenderer` module) as an
// explicit parameter rather than importing it itself, so the module is
// unit-testable with a mock ipcRenderer and so the call sites retain
// their existing `import { ipcRenderer } from "electron"` line (the
// preload scripts are the only place in the renderer bundle that's
// allowed to import from "electron" directly — keeping that import at
// the call site makes the security boundary visible).
//
// Channel list (shared):
//   - bubble:level            (renderer ← main, level stream)
//   - bubble:show-from-renderer (renderer → main, request show)
//   - bubble:ready            (renderer → main, signal ready)
//   - set_bubble_position     (renderer → main, top/bottom)
//   - bubble:draggable        (both directions — send + on)
//   - bubble:show             (renderer ← main, enter animation)
//   - bubble:hide             (renderer ← main, exit animation)
//   - bubble:move-by          (renderer → main, a11y keyboard move)
//
// Restricted (bubble window only):
//   - bubble:set-state        (renderer ← main, status pill state)
//   - bubble:config           (renderer ← main, bubble-relevant config)
//   - bubble:hidden           (renderer → main, exit-animation done)
//   - bubble:resize           (renderer → main, resize to pill bounds)
//   - bubble:toggle-dictation (renderer → main, mic button)
//   - bubble:dismiss          (renderer → main, × button)
//
// The restricted set is omitted from `preload/index.ts` so a
// compromised main renderer cannot invoke bubble-only channels
// (defense-in-depth — the main-process handlers also assert the
// sender's frame label, but the preload gate is the first line).

import type { IpcRenderer, IpcRendererEvent } from "electron";

/** Shape of the level payload delivered on the `bubble:level` channel. */
interface BubbleLevelPayload {
	rms: number;
	peak: number;
}

/**
 * Register a listener on `ipc` for `channel` that unwraps the IPC
 * `(...args)` payload via `transform` and forwards the result to
 * `callback`. Returns an unsubscribe function that removes the
 * listener (matching the public API of every `onXxx` bubble channel).
 *
 * Extracted from the per-channel inline `ipcRenderer.on(...) +
 * return () => ipcRenderer.removeListener(...)` boilerplate that was
 * duplicated 6× across `bubble.ts` + `index.ts`.
 */
export function makeListener<T>(
	ipc: IpcRenderer,
	channel: string,
	transform: (data: unknown) => T,
): (callback: (data: T) => void) => () => void {
	return (callback: (data: T) => void) => {
		const handler = (_event: IpcRendererEvent, data: unknown) =>
			callback(transform(data));
		ipc.on(channel, handler);
		return () => {
			ipc.removeListener(channel, handler);
		};
	};
}

/**
 * Register a no-arg listener on `ipc` for `channel` (channels that
 * carry no payload — `bubble:show` / `bubble:hide`). Returns an
 * unsubscribe function. Same shape as :func:`makeListener` but for
 * the zero-payload case (the IPC `(...args)` are ignored).
 */
export function makeVoidListener(
	ipc: IpcRenderer,
	channel: string,
): (callback: () => void) => () => void {
	return (callback: () => void) => {
		const handler = () => callback();
		ipc.on(channel, handler);
		return () => {
			ipc.removeListener(channel, handler);
		};
	};
}

export interface BubbleApi {
	onLevel: (callback: (data: BubbleLevelPayload) => void) => () => void;
	show: () => void;
	signalReady: () => void;
	setPosition: (position: "top" | "bottom") => void;
	setDraggable: (draggable: boolean) => void;
	onShow: (callback: () => void) => () => void;
	onHide: (callback: () => void) => () => void;
	onDraggable: (callback: (draggable: boolean) => void) => () => void;
	moveBy: (deltaX: number, deltaY: number) => void;
}

export interface RestrictedBubbleApi {
	onSetState: (callback: (state: string) => void) => () => void;
	onConfig: (callback: (cfg: Record<string, unknown>) => void) => () => void;
	hideComplete: () => void;
	resizeTo: (width: number, height: number) => void;
	toggleDictation: () => void;
	dismiss: () => void;
}

export type FullBubbleApi = BubbleApi & RestrictedBubbleApi;

export interface MakeBubbleApiOptions {
	/** When true, include the bubble-window-only restricted channels
	 * (`onSetState` / `onConfig` / `hideComplete` / `resizeTo` /
	 * `toggleDictation` / `dismiss`). When false, the returned object
	 * only has the shared channels — used by the main-renderer preload
	 * so a compromised main renderer cannot invoke bubble-only IPC. */
	includeRestricted: boolean;
}

/**
 * Build the `window.bubble` API surface for the given `ipc` instance.
 *
 * Shared factory used by both `preload/bubble.ts`
 * (`includeRestricted: true` — full bubble-window surface) and
 * `preload/index.ts` (`includeRestricted: false` — main-renderer
 * subset). The returned object is passed to
 * `contextBridge.exposeInMainWorld("bubble", ...)` by the caller.
 */
export function makeBubbleApi(
	ipc: IpcRenderer,
	opts: MakeBubbleApiOptions,
): FullBubbleApi | BubbleApi {
	const shared: BubbleApi = {
		onLevel: makeListener<BubbleLevelPayload>(
			ipc,
			"bubble:level",
			(d) => d as BubbleLevelPayload,
		),
		show: () => {
			ipc.send("bubble:show-from-renderer");
		},
		signalReady: () => {
			ipc.send("bubble:ready");
		},
		setPosition: (position: "top" | "bottom") => {
			ipc.send("set_bubble_position", position);
		},
		setDraggable: (draggable: boolean) => {
			ipc.send("bubble:draggable", draggable);
		},
		// ── Enter/exit animations ────────────────────────────────
		onShow: makeVoidListener(ipc, "bubble:show"),
		onHide: makeVoidListener(ipc, "bubble:hide"),
		onDraggable: makeListener<boolean>(ipc, "bubble:draggable", (d) =>
			Boolean(d),
		),
		// NEW-A11Y-006 (Round 0 forward-port): keyboard-based move
		// (accessibility alternative to drag). Main process clamps to
		// screen bounds.
		moveBy: (deltaX: number, deltaY: number) => {
			ipc.send("bubble:move-by", { deltaX, deltaY });
		},
	};

	if (!opts.includeRestricted) {
		return shared;
	}

	// Restricted channels — bubble window only.
	const restricted: RestrictedBubbleApi = {
		onSetState: makeListener<string>(ipc, "bubble:set-state", (s) => String(s)),
		// UX-10: receive bubble-relevant config (bubble_behavior /
		// bubble_click_to_toggle / bubble_mic_button) pushed from the
		// Python backend. The sandboxed bubble renderer has no get_config,
		// so this is how it learns whether to show the mic button.
		onConfig: makeListener<Record<string, unknown>>(
			ipc,
			"bubble:config",
			(c) => c as Record<string, unknown>,
		),
		hideComplete: () => {
			ipc.send("bubble:hidden");
		},
		// ── Auto-resize bubble window to match pill size ─────────
		// The BrowserWindow is 74x27 initially, but the pill content
		// is smaller.  We resize the window exactly to the pill bounds
		// so there's no invisible dead zone around the bubble that
		// blocks clicks to the windows underneath.
		resizeTo: (width: number, height: number) => {
			ipc.send("bubble:resize", { width, height });
		},
		// UX-10: toggle dictation from the bubble's own mic button. The
		// bubble is a sandboxed renderer (SEC-026) with NO `python.call`,
		// so it cannot invoke `toggle_dictation` directly. Instead it sends
		// a dedicated, single-purpose channel that the main process routes
		// to the Python backend. Restricted to the bubble frame by the
		// handler (assertFromBubble) so only the bubble can trigger it.
		toggleDictation: () => {
			ipc.send("bubble:toggle-dictation");
		},
		// BG-96: dismiss the bubble from its own '×' button. The bubble is
		// sandboxed (SEC-026) and has NO `python.call`, so it sends a
		// dedicated, single-purpose channel. The main-process handler
		// (bubble:dismiss) is owned by F11 — it should hide the bubble
		// window via the existing hideBubbleWindow() helper (and, when
		// bubble_behavior is always_visible, the bubble will stay hidden
		// until the next show() — typically the next dictation start).
		// Until F11 adds the handler, this IPC send is a no-op (no
		// listener registered on the main side) — safe by Electron's
		// default ipcMain behavior. Restricted to the bubble frame by the
		// handler (assertFromBubble) so only the bubble can dismiss
		// itself.
		dismiss: () => {
			ipc.send("bubble:dismiss");
		},
	};

	return { ...shared, ...restricted };
}
