// src/renderer/src/lib/tauri-bridge/window-namespace.ts
//
// ADR-0020 §6.3 (Phase 3 UI port): `window.window_` installer for the
// Tauri runtime.
//
// Basic window controls via Tauri's core window API. Export/dialog APIs
//( + ) invoke the Rust `export_history` /
// `export_vocabulary` / `export_templates` / `export_config` commands
// which use `tauri-plugin-dialog`'s save dialog. The return shape
// matches the Electron preload exactly:
//   - success → `{success: true, path: string}`
//   - user canceled → `{success: false}` (no path, no error)
//   - error → `{success: false, error: string}`
//
// The Rust command returns `{canceled: true}` on cancel (mapped to
// `{success: false}` here) or throws on error (caught and mapped to
// `{success: false, error}`). This keeps the renderer code (History.tsx,
// Vocabulary.tsx, Templates.tsx, Settings.tsx export buttons) unchanged
// on both paths.
//
// The previous version duplicated the ~28-line try/catch + canceled/error
// mapping 4× (exportHistory / exportVocabulary / exportTemplates /
// exportConfig). `makeExportCommand(cmd)` collapses each call site to a
// single factory invocation — the four methods now share a single
// implementation of the mapping.

import type { WindowBridge } from "@/types/ipc";
import type { ExportFormat } from "../../../../shared/export-format";

import { makeListener, type TauriGlobal } from "./detect";

/** Rust-side result envelope for `export_*` commands. */
interface ExportResult {
	success?: boolean;
	path?: string;
	canceled?: boolean;
	error?: string;
}

/** Return shape preserved across all four export methods (Electron parity). */
type ExportReturn = Promise<{
	success: boolean;
	path?: string;
	error?: string;
}>;

/**
 * Build a single export command ( + ). Eliminates the 4×
 * try/catch + canceled/error mapping duplication previously inlined in
 * `exportHistory` / `exportVocabulary` / `exportTemplates` /
 * `exportConfig`.
 *
 * The returned function is structurally assignable to all four
 * `WindowBridge` export slots — for `exportHistory` / `exportVocabulary`
 * the caller always passes the `format` arg (required by the type), for
 * `exportTemplates` / `exportConfig` the caller omits it (the factory's
 * `format?` parameter accepts both call shapes).
 *
 * @param cmd   Rust command name, e.g. `"export_history"`.
 */
function makeExportCommand(tauri: TauriGlobal, cmd: string) {
	return async (data: unknown, format?: ExportFormat): ExportReturn => {
		try {
			const result = await tauri.core.invoke<ExportResult>(
				cmd,
				format ? { data, format } : { data },
			);
			if (result?.canceled) {
				// User dismissed the save dialog — matches Electron's
				// `{success: false}` (no error, no path).
				return { success: false };
			}
			if (result?.error) {
				return { success: false, error: result.error };
			}
			return {
				success: Boolean(result?.success),
				path: result?.path,
			};
		} catch (e) {
			return {
				success: false,
				error: e instanceof Error ? e.message : String(e),
			};
		}
	};
}

/**
 * Build the `window.window_` namespace using Tauri's global API.
 *
 * `onMaximizedChanged` is implemented via `onResized` + `isMaximized()`
 * because Tauri v2 lacks a direct "maximized-changed" event — any
 * resize (including maximize/unmaximize) fires `onResized`, after which
 * we query the current maximized state and forward it to the consumer.
 */
export function createWindowNamespace(tauri: TauriGlobal): WindowBridge {
	const tauriWindow = tauri.window.getCurrentWindow();
	return {
		minimize: () => tauriWindow.minimize(),
		toggleMaximize: async () => {
			await tauriWindow.toggleMaximize();
			return tauriWindow.isMaximized();
		},
		close: () => tauriWindow.close(),
		isMaximized: () => tauriWindow.isMaximized(),
		onMaximizedChanged: (callback) =>
			makeListener<boolean>(
				(handler) =>
					tauriWindow.onResized(async () => {
						const maximized = await tauriWindow.isMaximized();
						handler(maximized);
					}),
				callback,
			),

		//invoke the Rust `export_history` command, which opens
		// `tauri-plugin-dialog`'s save dialog and writes the file. The
		// renderer call sites (History.tsx export button) are unchanged
		// because the return shape matches Electron's `history:export`
		// IPC handler (`{success, path?, error?}`).
		exportHistory: makeExportCommand(tauri, "export_history"),

		//invoke the Rust `export_vocabulary` command. Same
		// return-shape mapping as `exportHistory`. The renderer call
		// site (Vocabulary.tsx export button) is unchanged.
		exportVocabulary: makeExportCommand(tauri, "export_vocabulary"),

		// GDPR right-to-export for templates.
		// Invokes the Rust `export_templates` command (save-file dialog
		// + JSON write). Same return-shape mapping as `exportHistory`.
		// The renderer call site (Templates.tsx export button) is
		// unchanged on both Electron and Tauri paths.
		exportTemplates: makeExportCommand(tauri, "export_templates"),

		// GDPR right-to-export for the full
		// config. API keys are redacted by the Python sidecar before
		// the data reaches this command. Same shape as
		// `exportTemplates`.
		exportConfig: makeExportCommand(tauri, "export_config"),

		// open the Voice Typer log directory in the OS
		// file manager. Invokes the Rust `open_logs` command which
		// shells out to `explorer.exe` / `open` / `xdg-open`. The
		// renderer call site (Settings.tsx viewLogs button) is
		// unchanged on both paths.
		openLogs: async () => {
			try {
				const result = await tauri.core.invoke<{
					success: boolean;
					path?: string;
					error?: string;
				}>("open_logs");
				return {
					success: Boolean(result?.success),
					error: result?.error,
				};
			} catch (e) {
				return {
					success: false,
					error: e instanceof Error ? e.message : String(e),
				};
			}
		},

		//(MODEL-IMPORT): native folder picker for HuggingFace
		// model imports. Invokes the Rust `open_model_import_dialog`
		// command which uses `tauri-plugin-dialog`'s folder picker.
		// The renderer call site (Models.tsx import button) is
		// unchanged on both paths.
		openModelImportDialog: async () => {
			try {
				const result = await tauri.core.invoke<{
					canceled: boolean;
					path?: string;
				}>("open_model_import_dialog");
				return {
					canceled: Boolean(result?.canceled),
					path: result?.path,
				};
			} catch {
				// Surface errors as a canceled pick with no path — the
				// renderer treats both shapes the same (no-op on cancel
				// / error).
				return { canceled: true };
			}
		},

		// Push the renderer's current locale to the Rust host so it can
		// localize its native surfaces. Mirrors Electron's
		// `i18n:set-locale` IPC handler (main-process storage); the
		// Rust host stores the value in `SidecarState::host_locale` via
		// the `set_host_locale` command and resolves the same
		// `{ok: boolean; error?: string}` envelope instead of
		// rejecting, so a failed push never becomes an unhandled
		// rejection.
		setLocale: async (locale: string) => {
			try {
				return await tauri.core.invoke<{
					ok: boolean;
					error?: string;
				}>("set_host_locale", { locale });
			} catch (e) {
				return {
					ok: false,
					error: e instanceof Error ? e.message : String(e),
				};
			}
		},

		//`openElectronLogs` removed from the WindowBridge interface
		// (dead code — the Rust host's `open_host_logs` command was
		// deleted as dead code; the "View Logs" UX uses `open_logs`
		// which opens the config root).

		//(Tauri parity,  / ): forward a
		// renderer-caught error (e.g. React's `componentDidCatch` in
		// `ErrorBoundary.tsx`) to the Rust host for persistence.
		// Under Electron this routes via `renderer:log-error` IPC and
		// the main process appends to `electron-renderer-errors.log`.
		//
		//implemented at `commands/system_cmds.rs::renderer_log_error`
		//(), registered in `main.rs:244-245` (`renderer_log_error`
		// entry in the `generate_handler!` list). The Rust command
		// serializes the payload via `serde_json::to_string` and writes
		// it to the host file log via `log::error!("[RENDERER_ERROR] {}")`
		// (rotating 5 MB × 5 file logger in `platform/logging.rs`).
		//
		// The `.catch(() => {})` swallow is intentional and matches
		// the ErrorBoundary's own `logError(...).catch(...)` swallow
		// pattern: the `console.error("[ErrorBoundary] Caught render
		// error:", ...)` call has ALREADY surfaced the error to the
		// dev-tools + main-process console-forwarding path, so a
		// failing persistence command must never crash the
		// ErrorBoundary itself.
		logError: (payload) =>
			tauri.core.invoke<void>("renderer_log_error", payload).catch(() => {
				// Best-effort: never let the persistence path
				// crash the ErrorBoundary. The
				// `console.error` in `componentDidCatch` already
				// surfaced the error to the dev-tools +
				// main-process console forwarding path, so a
				// failing Rust command is invisible
				// to the user (intentional — see comment above).
			}),
	};
}
