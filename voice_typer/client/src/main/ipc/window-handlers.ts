/**
 * Window-control + dialog IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - window:minimize / window:toggle-maximize / window:close / window:is-maximized
 *     (custom title bar controls)
 *   - window:open-logs (UX-008: open the Python log directory in the OS file manager)
 *   - window:open-electron-logs (G4-M-71: open the Electron userData directory)
 *   - model:import-dialog (MODEL-IMPORT: native folder picker for HuggingFace imports)
 *   - renderer:log-error (G4-M-69: renderer → main error persistence)
 *
 * PVT-G5-068: the stale `i18n:set-locale` and `window:show` handlers were
 * removed — grep across `voice_typer/client/src/preload/` and
 * `voice_typer/client/src/renderer/` returns zero callers for either
 * channel. `i18n:set-locale` was superseded by the `set_tray_locale`
 * dispatch command (which pushes the locale to the Python backend, and
 * the main process reads its own locale from disk on startup via
 * `setMainLocale`). `window:show` was a tray "Open app" bridge that no
 * longer has a caller (the tray path now goes through the Rust host
 * under Tauri, and Electron's tray path uses `showMainWindow()`
 * directly).
 */
import { app, dialog, ipcMain, shell } from "electron";
import { mainT } from "../i18n";
import { appendLogLine, logger, rendererErrorsLogPath } from "../logging";
import { computeConfigDir } from "../single_instance";
import { state } from "../state";

// Saved bounds for window:toggle-maximize to restore on unmaximize.
// PVT-G5-FA15: this used to live on `state.preMaximizeBounds` but no
// other module reads/writes it — kept local for encapsulation (matches
// session-4 + session-5 consensus; session-1's `state.preMaximizeBounds`
// refactor was reverted by session-5's dead-code cleanup).
let preMaximizeBounds: Electron.Rectangle | null = null;

/**
 * DE-85: scrub potential PII from a React `componentStack` string
 * before writing it to `electron-renderer-errors.log`.
 *
 * React componentStack strings can include string-literal prop values
 * (e.g. `in Transcription text='user utterance' (created by App)`) in
 * development and, depending on bundler config, even in production.
 * This function replaces any `prop='value'` / `prop="value"` /
 * `prop={expr}` with `prop='[scrubbed]'` / `prop="[scrubbed]"` /
 * `prop={[scrubbed]}` so user data embedded in prop values does not
 * land in the log file.
 *
 * Best-effort: the regex handles the common cases (single/double
 * quoted strings, braced expressions without nested braces). Non-
 * string PII (e.g. user data in a JSON-stringified prop, or in the
 * `message`/`stack` fields) may still slip through — the log file
 * must be treated as sensitive regardless (see the reworded comment
 * on the `renderer:log-error` handler above).
 *
 * Exported so unit tests can exercise it directly.
 */
export function scrubComponentStackPii(s: string): string {
	return (
		s
			// prop='value' → prop='[scrubbed]'
			// prop="value" → prop="[scrubbed]"
			.replace(/(\b\w+)=(['"])[^'"]*\2/g, "$1=$2[scrubbed]$2")
			// prop={expr} → prop={[scrubbed]}
			// (single-level — nested braces are rare in componentStack)
			.replace(/(\b\w+)=\{[^}]*\}/g, "$1={[scrubbed]}")
	);
}

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

	// ── UX-008: Open Python log folder ────────────────────────────
	// Previously the Settings page's "View Logs" button just showed a
	// snackbar saying "Log folder opened" without actually opening
	// anything.  This handler opens the Python backend's log directory
	// in the OS file manager.  The path mirrors what
	// voice_typer/server/app.py:_setup_logging() writes to.
	//
	// CR-33 (fix): previously hardcoded `path.join(os.homedir(),
	// ".voice-typer")`, which (a) pointed at the WRONG directory on
	// platforms where `computeConfigDir()` returns a different path
	// (e.g. %APPDATA%/voice-typer on Windows, ~/Library/Application
	// Support/voice-typer on macOS), and (b) called `fs.mkdirSync(logDir,
	// { recursive: true })` which CREATED a stray `~/.voice-typer`
	// directory on every fresh install. Now we resolve the log
	// directory via `computeConfigDir()` (mirrors
	// `voice_typer/server/config.py:_config_dir()` and
	// `bootstrap.ts::setupUserData()`), and we NO LONGER create the
	// directory — the Python backend creates it on its own startup.
	//
	// G4-M-71: this handler opens the PYTHON backend's log dir. The
	// Electron main process has its own log folder (`app.getPath("userData")`)
	// where `electron-main.log`, `electron-crashes.log`, and
	// `electron-rejections.log` are written. That's exposed via the
	// separate `window:open-electron-logs` handler below so the Settings
	// UI can offer both as distinct buttons.
	ipcMain.handle("window:open-logs", async () => {
		try {
			const logDir = computeConfigDir();
			const result = await shell.openPath(logDir);
			if (result) {
				// openPath returns an error string on failure, empty string on success.
				return { success: false, error: result, path: logDir };
			}
			return { success: true, path: logDir };
		} catch (e: unknown) {
			logger.warn("window:open-logs failed", {
				error: (e as Error).message,
			});
			return { success: false, error: (e as Error).message };
		}
	});

	// ── G4-M-71: Open Electron log folder ────────────────────────
	// Opens `app.getPath("userData")` in the OS file manager. This is
	// where `electron-main.log` (G4-H-37 structured logger), the
	// `electron-crashes.log` / `electron-rejections.log` rotating
	// crash logs, and `electron-renderer-errors.log` (G4-M-67) live.
	// Distinct from `window:open-logs` above which opens the Python
	// backend's config dir (where `voice-typer.log` lives). Support
	// staff need BOTH locations to diagnose a crash end-to-end.
	ipcMain.handle("window:open-electron-logs", async () => {
		try {
			const logDir = app.getPath("userData");
			const result = await shell.openPath(logDir);
			if (result) {
				return { success: false, error: result, path: logDir };
			}
			return { success: true, path: logDir };
		} catch (e: unknown) {
			logger.warn("window:open-electron-logs failed", {
				error: (e as Error).message,
			});
			return { success: false, error: (e as Error).message };
		}
	});

	// ── Model import folder picker (MODEL-IMPORT) ───────────────────
	// Opens a native folder-selection dialog so the user can pick a
	// directory containing HuggingFace model cache folders to import.
	//
	// G4-H-22: `dialog.showOpenDialog` can reject (Linux with no display
	// server, internal Electron error, etc.). Previously this rejection
	// became an unhandled promise rejection that the SEC-021 breaker
	// counted toward the 5-error crash-loop exit threshold — a single
	// failed folder-picker could force-exit the app. We now wrap the
	// body in try/catch and return a structured `{ canceled: true,
	// error?: string }` envelope so the renderer can show a snackbar
	// instead of the whole app dying.
	ipcMain.handle("model:import-dialog", async () => {
		try {
			const { canceled, filePaths } = await dialog.showOpenDialog({
				title: mainT("dialog.selectModelFolder.title"),
				properties: ["openDirectory"],
			});
			if (canceled || !filePaths || filePaths.length === 0) {
				return { canceled: true };
			}
			return { canceled: false, path: filePaths[0] };
		} catch (e: unknown) {
			logger.warn("model:import-dialog failed", {
				error: (e as Error).message,
			});
			return {
				canceled: true,
				error: (e as Error).message,
			};
		}
	});

	// ── G4-M-69: renderer → main error persistence ────────────────
	// The sandboxed renderer (contextIsolation: true, sandbox: true)
	// cannot write to the userData directory directly. React's
	// `componentDidCatch` (in ErrorBoundary.tsx) forwards caught
	// render errors here so they land in `electron-renderer-errors.log`
	// alongside the ERROR-level console messages persisted by the
	// main-window `console-message` handler (G4-M-67).
	//
	// DE-85: payload MAY contain PII — treat as sensitive. React
	// `componentStack` strings can include string-literal prop
	// values (e.g. `<Transcription text='user utterance'>`) in
	// development and, depending on bundler config, even in
	// production. The `message` and `stack` fields can also
	// embed user data if the renderer logged it. The log file
	// lives in `userData/` and is readable by any same-user
	// process. Support staff who ask users to share these logs
	// should be aware of the PII surface. A best-effort scrubber
	// (scrubComponentStackPii) strips string-literal prop values
	// from `componentStack` before writing; non-string PII may
	// still slip through.
	//
	// Payload fields:
	//   - kind: "react-render" | "uncaught" | "unhandledrejection"
	//     (categorizes the source so the log is greppable)
	//   - stack: the Error.stack string (may be undefined for non-Error
	//     throws)
	//   - componentStack: React's component tree trace (only present
	//     for `componentDidCatch` calls) — scrubbed before writing
	//   - message: short summary for one-line log scanning
	//
	// The handler always resolves with `{ ok: true }` so the renderer's
	// `.catch(() => {})` swallow on the IPC call doesn't fire —
	// persistence is best-effort and the renderer shouldn't care if it
	// failed.
	ipcMain.handle(
		"renderer:log-error",
		async (
			_event,
			payload: {
				kind?: unknown;
				stack?: unknown;
				componentStack?: unknown;
				message?: unknown;
			},
		) => {
			try {
				const kind =
					typeof payload?.kind === "string" ? payload.kind : "unknown";
				const message =
					typeof payload?.message === "string" ? payload.message : "";
				const stack = typeof payload?.stack === "string" ? payload.stack : "";
				const componentStack =
					typeof payload?.componentStack === "string"
						? scrubComponentStackPii(payload.componentStack)
						: "";
				const ts = new Date().toISOString();
				const line = `${ts} [renderer-error:${kind}] ${message}\n${
					stack ? `  stack: ${stack.replace(/\n/g, "\n    ")}\n` : ""
				}${
					componentStack
						? `  componentStack: ${componentStack.replace(/\n/g, "\n    ")}\n`
						: ""
				}`;
				appendLogLine(rendererErrorsLogPath(), line);
			} catch (e) {
				logger.warn("renderer:log-error persist failed", {
					error: (e as Error).message,
				});
			}
			return { ok: true };
		},
	);
}
