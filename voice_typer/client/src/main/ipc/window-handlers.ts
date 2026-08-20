/**
 * Window-control + dialog IPC handlers.
 *
 * Extracted from `index.ts` (REF-2). Registers:
 *   - window:minimize / window:toggle-maximize / window:close / window:is-maximized
 *     (custom title bar controls)
 *   - window:open-logs (: open the Python log directory in the OS file manager)
 *   - model:import-dialog (MODEL-IMPORT: native folder picker for HuggingFace imports)
 *   - renderer:log-error (: renderer → main error persistence)
 *   - i18n:set-locale (: renderer pushes its locale to the main process so
 *     native dialogs render in the user's selected language)
 *
 *  originally removed the `i18n:set-locale` handler because no
 * caller existed.  re-adds it: the renderer's `setLocale()` now pushes
 * the locale via this channel on every change AND on app startup (so a
 * restart with a saved non-English locale propagates to native dialogs
 * before the first dialog is shown). `window:show` is still removed —
 * the tray path goes through `showMainWindow()` directly.
 *
 * : the `window:open-electron-logs` handler (: open the
 * Electron userData directory) was removed — the preload bridge no
 * longer exposes an `openElectronLogs` entry, so the handler was
 * unreachable. The Tauri bridge's `openElectronLogs` impl (which
 * invoked the Rust `open_host_logs` command) was deleted in lockstep,
 * and `openElectronLogs?` was removed from the `WindowBridge` type
 * contract in `types/ipc.ts`. See `preload/index.ts` for the cleanup
 * index.
 */
import fs from "node:fs";
import path from "node:path";
import { dialog, ipcMain, shell } from "electron";
import { mainT } from "../i18n";
import { appendLogLine, logger, rendererErrorsLogPath } from "../logging";
import { computeConfigDir } from "../single_instance";
import { state } from "../state";
import {
	I18nChannels,
	ModelChannels,
	RendererChannels,
	WindowChannels,
} from "./channels";

//`setMainLocale` is resolved lazily inside the
// i18n:set-locale IPC handler (via dynamic `import("../i18n")`) rather
// than via a top-level static import. This serves two purposes:
//   1. Test isolation: the handler test (`i18n-set-locale-handler.test.ts`)
//      mocks `../i18n` with `vi.mock` + `vi.resetModules()`. A static
//      `import { setMainLocale }` captures the binding at module-load
//      time; under `vi.resetModules()` the ESM binding can become
//      stale, causing "setMainLocale is not defined" at handler
//      invocation. The dynamic `import()` resolves against the
//      CURRENT module registry at call time, so the mock is always
//      in effect.
//   2. Avoids a static-import cycle in environments that don't mock
//      `../i18n` (e.g. integration tests that load the real i18n
//      bundle). The static import would pull `../i18n` → `./branding`
//      → ... into the window-handlers module graph eagerly; the
//      dynamic import defers that cost to when the handler is
//      actually invoked (which is rare — only on locale change).

// Saved bounds for window:toggle-maximize to restore on unmaximize.
//this used to live on `state.preMaximizeBounds` but no
// other module reads/writes it — kept local for encapsulation (matches
// session-4 + session-5 consensus; session-1's `state.preMaximizeBounds`
// refactor was reverted by session-5's dead-code cleanup).
let preMaximizeBounds: Electron.Rectangle | null = null;

/**
 * : scrub potential PII from a React `componentStack` string
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
	// Idempotent registration: removeHandler is a no-op if no handler
	// is registered for the channel. Optional chaining tolerates test
	// mocks that don't expose `removeHandler`.
	ipcMain.removeHandler?.(WindowChannels.minimize);
	ipcMain.handle(WindowChannels.minimize, () => {
		state.mainWindow?.minimize();
	});

	ipcMain.removeHandler?.(WindowChannels.toggleMaximize);
	ipcMain.handle(WindowChannels.toggleMaximize, async () => {
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

	ipcMain.removeHandler?.(WindowChannels.close);
	ipcMain.handle(WindowChannels.close, () => {
		state.mainWindow?.close();
	});

	ipcMain.removeHandler?.(WindowChannels.isMaximized);
	ipcMain.handle(WindowChannels.isMaximized, () => {
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
	// directory via `computeConfigDir()` + `/logs` (mirrors
	// `voice_typer/server/config.py:_config_dir()` and
	// `bootstrap.ts::setupUserData()`), and we NO LONGER create the
	// directory — the Python backend creates it on its own startup.
	//
	// DT-51: the sibling `window:open-electron-logs` handler (which
	// opened the Electron userData dir) was removed — the preload
	// bridge no longer exposes an `openElectronLogs` entry, so the
	// handler was unreachable. The Tauri bridge's
	// `openElectronLogs` impl (which invoked the Rust
	// `open_host_logs` command) was deleted in lockstep.
	ipcMain.removeHandler?.(WindowChannels.openLogs);
	ipcMain.handle(WindowChannels.openLogs, async () => {
		try {
			// O1: the logs live under `<config-dir>/logs`.
			const logDir = path.join(computeConfigDir(), "logs");
			const stat = fs.statSync(logDir, { throwIfNoEntry: false });
			if (!stat?.isDirectory()) {
				return { success: false, error: "log directory not found" };
			}
			const result = await shell.openPath(logDir);
			if (result) {
				// openPath returns an error string on failure, empty string on success.
				return { success: false, error: result };
			}
			return { success: true };
		} catch (e: unknown) {
			logger.warn("window:open-logs failed", {
				error: (e as Error).message,
			});
			return { success: false, error: String(e) };
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
	ipcMain.removeHandler?.(ModelChannels.importDialog);
	ipcMain.handle(ModelChannels.importDialog, async () => {
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
	ipcMain.removeHandler?.(RendererChannels.logError);
	ipcMain.handle(
		RendererChannels.logError,
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

	// ── XA-20-10 / NH-3: renderer → main locale sync ────────────────
	// The renderer pushes its locale here so the main process can:
	//   (1) Localize native Electron dialogs via `setMainLocale` (the
	//       main-process i18n bundle in `main/i18n.ts`).
	//   (2) Forward the locale to the bubble BrowserWindow via
	//       `notifyBubbleLocaleChanged` so its separate JS context
	//       re-renders in the new locale without a full reload.
	//
	// Payload contract (Rule 26 / P4 — IPC types must match): the
	// handler accepts a BARE STRING only ("ar", "en-US", …). This
	// matches the ONLY production caller — the preload's
	// `setLocale: (locale: string) =>
	// ipcRenderer.invoke(I18nChannels.setLocale, locale)` — which
	// always passes a bare string. The previous implementation also
	// accepted `{ locale: string }` for legacy/test-contract reasons,
	// but that dual-shape acceptance violates Rule 26/P4 (the
	// renderer-side type union is `string`, not `string | { locale:
	// string }`) and lets a compromised renderer probe the handler's
	// shape with object payloads. Empty / null / non-string
	// payloads return `{ ok: false, error: "empty locale" }` so the
	// renderer's `.catch(() => {})` swallow doesn't fire — the push
	// is best-effort.
	//
	// NOTE: the `i18n-set-locale-handler.test.ts` suite (at
	// `src/main/__tests__/i18n-set-locale-handler.test.ts`) was
	// updated in lockstep with this tightening — it now asserts
	// the `{ locale: string }` object shape is REJECTED
	// ("rejects a {locale} object payload (bare-string-only
	// contract)"). Keep the test and the handler's shape in
	// sync if either changes.
	//
	// The handler is async because the bubble notification uses a
	// dynamic import (to avoid a static-import cycle in test
	// environments that don't mock the bubble-window module).
	// Annotate the IPC payload as `unknown` (mirrors the
	// `python-call` handler at `python-call-handler.ts:68`, which
	// types its `msg` arg as `Record<string, unknown>`). Electron's
	// `ipcMain.handle` listener signature types the variadic args as
	// `any[]`, which silently propagates into the handler body and
	// defeats type-checking on every property access. Marking the
	// payload `unknown` forces every read through a runtime guard —
	// the existing `typeof payload === "string"` narrowing already
	// does this, so no body changes are required; the only
	// behavioural change is that a future edit that touches
	// `payload` without narrowing first will fail at compile time
	// instead of compiling silently.
	ipcMain.removeHandler?.(I18nChannels.setLocale);
	ipcMain.handle(I18nChannels.setLocale, async (_event, payload: unknown) => {
		// Bare-string only — see Rule 26/P4 note above.
		if (typeof payload !== "string" || payload.length === 0) {
			return { ok: false, error: "empty locale" };
		}
		const locale = payload;
		// XA-20-10 / NH-3: resolve setMainLocale via dynamic
		// import so the test's vi.mock is applied at call time
		// (see the long comment near the top of this module).
		let setMainLocale: (locale: string) => void;
		try {
			const i18nMod = await import("../i18n");
			setMainLocale =
				typeof i18nMod.setMainLocale === "function"
					? i18nMod.setMainLocale
					: () => {};
		} catch {
			setMainLocale = () => {};
		}
		try {
			setMainLocale(locale);
		} catch (e) {
			// Defensive — setMainLocale currently never throws, but
			// a future refactor must not crash the main process.
			return { ok: false, error: (e as Error).message };
		}
		// XA-20-10: forward to the bubble BrowserWindow so its
		// separate JS context can re-render in the new locale.
		// Dynamic import avoids a static-import cycle in tests
		// that mock `../i18n` + `../state` but not
		// `../windows/bubble-window`.
		try {
			const { notifyBubbleLocaleChanged } = await import(
				"../windows/bubble-window"
			);
			notifyBubbleLocaleChanged(locale);
		} catch (e) {
			// Best-effort — bubble may not be loaded yet (early
			// startup) or the dynamic import may fail in test
			// environments. The locale still took effect for
			// native dialogs via setMainLocale above.
			logger.warn("i18n:set-locale: bubble notification failed", {
				error: (e as Error).message,
			});
		}
		return { ok: true };
	});
}
