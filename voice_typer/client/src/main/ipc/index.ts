/**
 * IPC handler registration entry point.
 *
 * Extracted from `index.ts` (REF-2). `registerIpcHandlers()` is called
 * once at module-load time from `index.ts` to register every
 * `ipcMain.on` / `ipcMain.handle` listener. Behaviour is identical to
 * the original top-level registrations.
 */
import { registerBubbleHandlers } from "./bubble-handlers";
import { registerExportHandlers } from "./export-handlers";
import { registerPythonCallHandler } from "./python-call-handler";
import { registerWindowHandlers } from "./window-handlers";

export function registerIpcHandlers(): void {
	registerBubbleHandlers();
	registerWindowHandlers();
	registerExportHandlers();
	registerPythonCallHandler();
}
