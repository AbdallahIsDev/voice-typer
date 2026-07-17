/**
 * Window-control + dialog IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - window:minimize / window:toggle-maximize / window:close / window:is-maximized
 *     (custom title bar controls)
 *   - window:show (tray "Open app" IPC bridge)
 *   - window:open-logs (UX-008: open the Python log directory in the OS file manager)
 *   - model:import-dialog (MODEL-IMPORT: native folder picker for HuggingFace imports)
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { dialog, ipcMain, shell } from "electron";
import { state } from "../state";
import { showMainWindow } from "../windows";

let preMaximizeBounds: Electron.Rectangle | null = null;

export function registerWindowHandlers(): void {
	ipcMain.handle("window:minimize", () => {
		state.mainWindow?.minimize();
	});

	ipcMain.handle("window:toggle-maximize", async () => {
		const win = state.mainWindow;
		if (!win) return false;

		if (win.isMaximized()) {
			win.unmaximize();
			if (preMaximizeBounds) {
				win.setBounds(preMaximizeBounds);
				preMaximizeBounds = null;
			}
		} else {
			preMaximizeBounds = win.getBounds();
			win.maximize();
		}
		return win.isMaximized();
	});

	ipcMain.handle("window:close", () => {
		state.mainWindow?.close();
	});

	ipcMain.handle("window:is-maximized", () => {
		return state.mainWindow?.isMaximized() ?? false;
	});

	// ── UX-008: Open log folder ─────────────────────────────────────
	// Previously the Settings page's "View Logs" button just showed a
	// snackbar saying "Log folder opened" without actually opening
	// anything.  This handler opens the Python backend's log directory
	// in the OS file manager.  The path mirrors what
	// voice_typer/server/app.py:_setup_logging() writes to.
	ipcMain.handle("window:open-logs", async () => {
		try {
			// Mirror voice_typer/server/config.py:_config_dir()
			const logDir = path.join(os.homedir(), ".voice-typer");
			// Create the directory if it doesn't exist yet (first run).
			try {
				fs.mkdirSync(logDir, { recursive: true });
			} catch {
				/* ignore */
			}
			const result = await shell.openPath(logDir);
			if (result) {
				// openPath returns an error string on failure, empty string on success.
				return { success: false, error: result, path: logDir };
			}
			return { success: true, path: logDir };
		} catch (e: unknown) {
			return { success: false, error: (e as Error).message };
		}
	});

	// ── Model import folder picker (MODEL-IMPORT) ───────────────────
	// Opens a native folder-selection dialog so the user can pick a
	// directory containing HuggingFace model cache folders to import.
	ipcMain.handle("model:import-dialog", async () => {
		const { canceled, filePaths } = await dialog.showOpenDialog({
			title: "Select Model Folder",
			properties: ["openDirectory"],
		});
		if (canceled || !filePaths || filePaths.length === 0) {
			return { canceled: true };
		}
		return { canceled: false, path: filePaths[0] };
	});

	// Allow the Python backend (tray "Open app") to request showing the
	// dashboard over TCP — a clean, single-hop alternative to the Win32
	// EnumWindows focus hack in tray._bring_electron_to_front.  The tray
	// tries this first; the Win32 path remains as a fallback.
	ipcMain.handle("window:show", () => {
		showMainWindow();
		return true;
	});
}
