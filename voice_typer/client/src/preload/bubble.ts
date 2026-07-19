import { contextBridge, ipcRenderer } from "electron";

// SEC-026: dedicated preload for the bubble window. The bubble renderer
// only needs the `bubble:` IPC channels (level, show, hide, draggable,
// position, drag). It does NOT need access to `python.call` (which can
// send arbitrary IPC commands to the Python backend) or `window_.*`
// (which can minimize / maximize / close the main window and trigger
// history/vocabulary exports).
//
// Splitting the preload so the bubble renderer can only call the small
// set of bubble-specific channels means that even if the bubble is
// compromised (e.g. an XSS payload in the waveform renderer), the
// attacker cannot invoke `python.call({type:"quit_app"})` or
// `python.call({type:"set_config", data:{...}})` to take over the
// app. Previously both windows loaded `preload/index.ts` which
// exposed all three namespaces.

contextBridge.exposeInMainWorld("bubble", {
	onLevel: (callback: (data: { rms: number; peak: number }) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, data: unknown) =>
			callback(data as { rms: number; peak: number });
		ipcRenderer.on("bubble:level", handler);
		return () => {
			ipcRenderer.removeListener("bubble:level", handler);
		};
	},
	show: () => {
		ipcRenderer.send("bubble:show-from-renderer");
	},
	signalReady: () => {
		ipcRenderer.send("bubble:ready");
	},
	setPosition: (position: "top" | "bottom") => {
		ipcRenderer.send("set_bubble_position", position);
	},
	setDraggable: (draggable: boolean) => {
		ipcRenderer.send("bubble:draggable", draggable);
	},
	// ── Drag-to-move ─────────────────────────────────────────
	startDrag: () => {
		ipcRenderer.send("bubble:drag-start");
	},
	drag: (deltaX: number, deltaY: number) => {
		ipcRenderer.send("bubble:drag", { deltaX, deltaY });
	},
	endDrag: () => {
		ipcRenderer.send("bubble:drag-end");
	},
	// ── Enter/exit animations ────────────────────────────────
	onShow: (callback: () => void) => {
		const handler = () => callback();
		ipcRenderer.on("bubble:show", handler);
		return () => {
			ipcRenderer.removeListener("bubble:show", handler);
		};
	},
	onHide: (callback: () => void) => {
		const handler = () => callback();
		ipcRenderer.on("bubble:hide", handler);
		return () => {
			ipcRenderer.removeListener("bubble:hide", handler);
		};
	},
	onSetState: (callback: (state: string) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, state: unknown) =>
			callback(String(state));
		ipcRenderer.on("bubble:set-state", handler);
		return () => {
			ipcRenderer.removeListener("bubble:set-state", handler);
		};
	},
	onDraggable: (callback: (draggable: boolean) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, draggable: unknown) =>
			callback(Boolean(draggable));
		ipcRenderer.on("bubble:draggable", handler);
		return () => {
			ipcRenderer.removeListener("bubble:draggable", handler);
		};
	},
	// UX-10: receive bubble-relevant config (bubble_behavior /
	// bubble_click_to_toggle / bubble_mic_button) pushed from the
	// Python backend. The sandboxed bubble renderer has no get_config,
	// so this is how it learns whether to show the mic button.
	onConfig: (callback: (cfg: Record<string, unknown>) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, cfg: unknown) =>
			callback(cfg as Record<string, unknown>);
		ipcRenderer.on("bubble:config", handler);
		return () => {
			ipcRenderer.removeListener("bubble:config", handler);
		};
	},
	hideComplete: () => {
		ipcRenderer.send("bubble:hidden");
	},
	// ── Auto-resize bubble window to match pill size ─────────
	// The BrowserWindow is 74x27 initially, but the pill content
	// is smaller.  We resize the window exactly to the pill bounds
	// so there's no invisible dead zone around the bubble that
	// blocks clicks to the windows underneath.
	resizeTo: (width: number, height: number) => {
		ipcRenderer.send("bubble:resize", { width, height });
	},
	// NEW-A11Y-006 (Round 0 forward-port): keyboard-based move
	// (accessibility alternative to drag). Main process clamps to
	// screen bounds. Mirrors the implementation in preload/index.ts —
	// included here so the bubble preload (which is intentionally
	// narrower than preload/index.ts) still exposes the accessibility
	// move channel that Bubble.tsx:210 relies on. Without this,
	// ``window.bubble?.moveBy?.(...)`` silently no-ops and arrow-key
	// bubble repositioning is dead in production.
	moveBy: (deltaX: number, deltaY: number) => {
		ipcRenderer.send("bubble:move-by", { deltaX, deltaY });
	},
	// UX-10: toggle dictation from the bubble's own mic button. The
	// bubble is a sandboxed renderer (SEC-026) with NO `python.call`,
	// so it cannot invoke `toggle_dictation` directly. Instead it sends
	// a dedicated, single-purpose channel that the main process routes
	// to the Python backend. Restricted to the bubble frame by the
	// handler (assertFromBubble) so only the bubble can trigger it.
	toggleDictation: () => {
		ipcRenderer.send("bubble:toggle-dictation");
	},
});
