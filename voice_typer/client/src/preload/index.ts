import { contextBridge, ipcRenderer } from "electron";
import { makeBubbleApi } from "./_bubble-channels";

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

// The `bubble` namespace is now built by the shared
// `makeBubbleApi` factory in `./_bubble-channels.ts`. The factory is
// called with `includeRestricted: false` here so the main renderer
// gets only the shared bubble channels (`onLevel` / `show` /
// `signalReady` / `setPosition` / `setDraggable` / `onShow` / `onHide`
// / `onDraggable` / `moveBy`). The restricted bubble-window-only
// channels (`onSetState` / `onConfig` / `hideComplete` / `resizeTo` /
// `toggleDictation` / `dismiss`) are NOT exposed on the main renderer
// — a compromised main renderer cannot invoke them. The bubble-window
// preload (`preload/bubble.ts`) calls the same factory with
// `includeRestricted: true` to get the full surface.
//
// `hideComplete` was previously removed from this main
// renderer's preload (only the bubble renderer's exit-animation
// handler should invoke `bubble:hidden`). The factory's
// `includeRestricted: false` path now codifies that removal — the
// channel is simply not in the returned object.
contextBridge.exposeInMainWorld(
	"bubble",
	makeBubbleApi(ipcRenderer, { includeRestricted: false }),
);

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
	// The return type is extended with `error?: string` so the
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
	// `openElectronLogs` was removed — no renderer call site existed
	// (verified by grep across `voice_typer/client/src/renderer`).
	// Cross-file cleanup:
	//   - types/ipc.ts: `openElectronLogs?` field removed
	//   - tauri-bridge/window-namespace.ts: `openElectronLogs:` impl removed
	//   - main/ipc/window-handlers.ts: `window:open-electron-logs`
	//     ipcMain.handle — owned by main-process agent (coordinate
	//     separately; leaving the handler installed is harmless since
	//     the preload bridge no longer exposes a way to invoke it).
	// Forward a renderer-caught error to the main process
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
