import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("python", {
	call: (msg: Record<string, unknown>) =>
		ipcRenderer.invoke("python-call", msg),
	onEvent: (callback: (msg: Record<string, unknown>) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, msg: unknown) =>
			callback(msg as Record<string, unknown>);
		ipcRenderer.on("python-event", handler);
		return () => {
			ipcRenderer.removeListener("python-event", handler);
		};
	},
});

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
	// NEW-A11Y-006: keyboard-based move (accessibility alternative to drag).
	// Main process clamps to screen bounds.
	moveBy: (deltaX: number, deltaY: number) => {
		ipcRenderer.send("bubble:move-by", { deltaX, deltaY });
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
	onDraggable: (callback: (draggable: boolean) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, draggable: unknown) =>
			callback(Boolean(draggable));
		ipcRenderer.on("bubble:draggable", handler);
		return () => {
			ipcRenderer.removeListener("bubble:draggable", handler);
		};
	},
	hideComplete: () => {
		ipcRenderer.send("bubble:hidden");
	},
});

contextBridge.exposeInMainWorld("window_", {
	minimize: () => ipcRenderer.invoke("window:minimize"),
	toggleMaximize: () =>
		ipcRenderer.invoke("window:toggle-maximize") as Promise<boolean>,
	close: () => ipcRenderer.invoke("window:close"),
	isMaximized: () =>
		ipcRenderer.invoke("window:is-maximized") as Promise<boolean>,
	onMaximizedChanged: (callback: (maximized: boolean) => void) => {
		const handler = (_event: Electron.IpcRendererEvent, maximized: unknown) =>
			callback(Boolean(maximized));
		ipcRenderer.on("window:maximized-changed", handler);
		return () => {
			ipcRenderer.removeListener("window:maximized-changed", handler);
		};
	},
	exportHistory: (data: Record<string, unknown>[], format: "json" | "csv") =>
		ipcRenderer.invoke("history:export", { data, format }) as Promise<{
			success: boolean;
			path?: string;
			error?: string;
		}>,
	exportVocabulary: (data: Record<string, unknown>, format: "json" | "csv") =>
		ipcRenderer.invoke("vocabulary:export", { data, format }) as Promise<{
			success: boolean;
			path?: string;
			error?: string;
		}>,
	// NEW-PRIV-007: GDPR right-to-export for templates + config.
	exportTemplates: (data: unknown) =>
		ipcRenderer.invoke("templates:export", { data }) as Promise<{
			success: boolean;
			path?: string;
			error?: string;
		}>,
	exportConfig: (data: unknown) =>
		ipcRenderer.invoke("config:export", { data }) as Promise<{
			success: boolean;
			path?: string;
			error?: string;
		}>,
	// MODEL-IMPORT: open a native folder picker for importing models.
	// G4-H-22: return type extended with `error?: string` so the
	// handler can surface a failure reason (Linux no-display, internal
	// Electron error) to the renderer instead of an unhandled rejection
	// that the SEC-021 breaker would count toward the 5-error crash-
	// loop exit threshold. The renderer treats `{canceled: true}` and
	// `{canceled: true, error: "..."}` identically (no-op on cancel),
	// but can optionally show a snackbar when `error` is present.
	openModelImportDialog: () =>
		ipcRenderer.invoke("model:import-dialog") as Promise<{
			canceled: boolean;
			path?: string;
			error?: string;
		}>,
	// UX-008: actually open the log folder in the OS file manager.
	// Previously the Settings page just showed a snackbar saying
	// "Log folder opened" without opening anything.
	openLogs: () =>
		ipcRenderer.invoke("window:open-logs") as Promise<{
			success: boolean;
			path?: string;
			error?: string;
		}>,
	// GT-54 (session-6): `openElectronLogs` was removed — no renderer
	// call site existed (verified by grep across
	// `voice_typer/client/src/renderer`). Cross-file cleanup:
	//   - types/ipc.ts: `openElectronLogs?` field — GT-FIX-16
	//   - tauri-bridge/window-namespace.ts: `openElectronLogs:` impl —
	//     GT-FIX-17
	//   - main/ipc/window-handlers.ts: `window:open-electron-logs`
	//     ipcMain.handle — owned by main-process agent (coordinate
	//     separately; leaving the handler installed is harmless since
	//     the preload bridge no longer exposes a way to invoke it).
	// G4-M-69: forward a renderer-caught error to the main process
	// for persistence in `electron-renderer-errors.log`. The main
	// process is the only side with filesystem access (sandboxed
	// renderer can't write to userData), so the ErrorBoundary's
	// `componentDidCatch` routes through this IPC channel. The
	// payload is intentionally minimal — no PII, just the kind
	// (react-render | uncaught | unhandledrejection), the stack,
	// and optional componentStack from React's ErrorInfo.
	logError: (payload: {
		kind: string;
		stack?: string;
		componentStack?: string;
		message?: string;
	}) => ipcRenderer.invoke("renderer:log-error", payload),
});
