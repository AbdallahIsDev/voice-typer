import { contextBridge, ipcRenderer } from "electron";
import {
	ExportChannels,
	I18nChannels,
	ModelChannels,
	PythonChannels,
	RendererChannels,
	WindowChannels,
} from "../main/ipc/channels";
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
			ipcRenderer.invoke(PythonChannels.call, msg),
		onEvent: (callback: (msg: Record<string, unknown>) => void) => {
			const handler = (_event: Electron.IpcRendererEvent, msg: unknown) =>
				callback(msg as Record<string, unknown>);
			ipcRenderer.on(PythonChannels.event, handler);
			return () => {
				ipcRenderer.removeListener(PythonChannels.event, handler);
			};
		},
	});

	contextBridge.exposeInMainWorld("window_", {
		minimize: () => ipcRenderer.invoke(WindowChannels.minimize),
		toggleMaximize: () =>
			ipcRenderer.invoke(WindowChannels.toggleMaximize) as Promise<boolean>,
		close: () => ipcRenderer.invoke(WindowChannels.close),
		isMaximized: () =>
			ipcRenderer.invoke(WindowChannels.isMaximized) as Promise<boolean>,
		onMaximizedChanged: (callback: (maximized: boolean) => void) => {
			const handler = (_event: Electron.IpcRendererEvent, maximized: unknown) =>
				callback(Boolean(maximized));
			ipcRenderer.on(WindowChannels.maximizedChanged, handler);
			return () => {
				ipcRenderer.removeListener(WindowChannels.maximizedChanged, handler);
			};
		},
		exportHistory: (data: Record<string, unknown>[], format: "json" | "csv") =>
			ipcRenderer.invoke(ExportChannels.history, { data, format }) as Promise<{
				success: boolean;
				path?: string;
				error?: string;
			}>,
		exportVocabulary: (data: Record<string, unknown>, format: "json" | "csv") =>
			ipcRenderer.invoke(ExportChannels.vocabulary, {
				data,
				format,
			}) as Promise<{
				success: boolean;
				path?: string;
				error?: string;
			}>,
		exportTemplates: (data: unknown) =>
			ipcRenderer.invoke(ExportChannels.templates, { data }) as Promise<{
				success: boolean;
				path?: string;
				error?: string;
			}>,
		exportConfig: (data: unknown) =>
			ipcRenderer.invoke(ExportChannels.config, { data }) as Promise<{
				success: boolean;
				path?: string;
				error?: string;
			}>,
		openModelImportDialog: () =>
			ipcRenderer.invoke(ModelChannels.importDialog) as Promise<{
				canceled: boolean;
				path?: string;
				error?: string;
			}>,
		openLogs: () =>
			ipcRenderer.invoke(WindowChannels.openLogs) as Promise<{
				success: boolean;
				path?: string;
				error?: string;
			}>,
		setLocale: (locale: string) =>
			ipcRenderer.invoke(I18nChannels.setLocale, locale) as Promise<{
				ok: boolean;
				error?: string;
			}>,
		logError: (payload: {
			kind: string;
			stack?: string;
			componentStack?: string;
			message?: string;
		}) => ipcRenderer.invoke(RendererChannels.logError, payload),
	});
}
