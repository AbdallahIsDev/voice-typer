import { contextBridge, ipcRenderer } from "electron";
import { makeBubbleApi } from "./_bubble-channels";

// SEC-026: single preload for both main and bubble windows.
// The preload reads `location.href` to determine which window we are
// in and exposes only the appropriate API surface.  The bubble window
// gets ONLY the `bubble` namespace (no `python.call`, no `window_.*`).
const isBubble =
	typeof location !== "undefined" && location.href.includes("bubble.html");

contextBridge.exposeInMainWorld(
	"bubble",
	makeBubbleApi(ipcRenderer, { includeRestricted: isBubble }),
);

if (!isBubble) {
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
		openModelImportDialog: () =>
			ipcRenderer.invoke("model:import-dialog") as Promise<{
				canceled: boolean;
				path?: string;
				error?: string;
			}>,
		openLogs: () =>
			ipcRenderer.invoke("window:open-logs") as Promise<{
				success: boolean;
				path?: string;
				error?: string;
			}>,
		setLocale: (locale: string) =>
			ipcRenderer.invoke("i18n:set-locale", locale) as Promise<{
				ok: boolean;
				error?: string;
			}>,
		logError: (payload: {
			kind: string;
			stack?: string;
			componentStack?: string;
			message?: string;
		}) => ipcRenderer.invoke("renderer:log-error", payload),
	});
}
