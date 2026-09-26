// types/ipc/bridge.ts
// The two preload-exposed bridge interfaces: `PythonBridge` (the
// `window.python` API surface) and `WindowBridge` (the
// `window.window_` API surface for the custom title bar + GDPR export
// helpers + native pickers).
// Imports `PythonPushEvent` from `./push_events` for the `onEvent`
// callback signature. The `declare global { interface Window { ... } }`
// augmentation that exposes these bridges on `window.python` /
// `window.window_` lives in `./bubble_bridge.ts` (TypeScript merges
// global augmentations across files).

import type { ExportFormat } from "../../../../shared/export-format";
import type { PythonPushEvent } from "./push_events";

// ── Window augmentation for type-safe python bridge ───────────────

export interface PythonBridge {
	call: (msg: {
		type: string;
		data?: Record<string, unknown>;
	}) => Promise<unknown>;
	onEvent: (callback: (event: PythonPushEvent) => void) => () => void;
}

// ── Window augmentation for the custom title bar (preload `window.*`) ─

export interface WindowBridge {
	minimize: () => Promise<void>;
	toggleMaximize: () => Promise<boolean>;
	close: () => Promise<void>;
	isMaximized: () => Promise<boolean>;
	onMaximizedChanged: (callback: (maximized: boolean) => void) => () => void;
	exportHistory: (
		data: Record<string, unknown>[],
		format: ExportFormat,
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	exportVocabulary: (
		data: Record<string, unknown>,
		format: ExportFormat,
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	//GDPR right-to-export for templates + config.
	exportTemplates?: (
		data: unknown,
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	exportConfig?: (
		data: unknown,
	) => Promise<{ success: boolean; path?: string; error?: string }>;
	openLogs?: () => Promise<{ success: boolean; error?: string }>;
	/** ADR-0023 media page: subscribe to native file-drop events, the
	 *  callback receives the dropped files' ABSOLUTE paths. Returns an
	 *  unsubscribe function (no-op when the webview API is unavailable). */
	onDragDropFiles?: (callback: (paths: string[]) => void) => () => void;
	//forward a renderer-caught error (e.g. from React's
	// `componentDidCatch`) to the host for persistence in the host file
	// log. The sandboxed renderer can't write to userData directly.
	// Optional so a bridge without that capability can omit it without
	// breaking the type contract.
	// Webview liveness beacon. The Rust host's
	// `renderer_heartbeat` command stamps the receipt; the watchdog
	// reports a stall when beats stop while the main window is visible.
	heartbeat?: () => Promise<void>;
	logError?: (payload: {
		kind: string;
		// `level` selects the log level on both runtimes (`"warn"` →
		// the WARN line, anything else → ERROR, the fail-loud default).
		// The console capture passes the captured method's own
		// name; the Rust `renderer_log_error` command and the predecessor
		// `renderer:log-error` handler both route on it, so a captured
		// `console.warn` is never promoted to an error.
		level?: string;
		stack?: string;
		componentStack?: string;
		message?: string;
		// Source position for a window `error` event
		// (`{file, line, column}`), forwarded by the shared
		// `globalErrorHandler` and rendered into the
		// `[renderer-error]` line's `(src=file:line:col)` suffix by the
		// Rust `renderer_log_error` command.
		location?: { file: string; line?: number; column?: number };
	}) => Promise<void>;
	//native folder picker for HuggingFace model imports. Was
	// missing from the type, Models.tsx accessed it via a runtime cast.
	// Declared optional because the Tauri bridge installs it but the
	// legacy predecessor preload also installs it (so the type is satisfied
	// on both paths).
	openModelImportDialog?: () => Promise<{
		canceled: boolean;
		path?: string;
		error?: string;
	}>;
	// Push the renderer's current locale to the host process so it can
	// localise native dialogs (single-instance error, critical-error
	// dialog, model-folder picker, export save-as dialogs). Implemented
	// by the Tauri bridge as the
	// `set_host_locale` command (stored in `SidecarState::host_locale`).
	// Optional because not every runtime context installs `window_`
	// (the sandboxed bubble window omits the namespace entirely).
	setLocale?: (locale: string) => Promise<unknown>;
	// Restart the Python sidecar process only (the Tauri host stays up
	// in dev; production uses host-owned restart).
	// Used by the "Lost connection" Retry escalation AFTER a plain
	// reconnect probe fails. The Tauri bridge installs it
	// (`restart_sidecar` command), so
	// one-click backend recovery works.
	restartBackend?: () => Promise<{
		ok: boolean;
		reason?: string;
	}>;
	// Share-stats image platform operations. The renderer captures the
	// PNG data URL itself; these bridge to the Tauri host for
	// filesystem / clipboard / shell access a sandboxed renderer cannot
	// use. Optional (the anchor-download / navigator.clipboard fallbacks
	// stay for runtimes that omit them):
	//   - saveStatsImage: mode "downloads" = instant save to the OS
	//     Downloads folder (no dialog); mode "saveAs" = native save dialog.
	//   - copyStatsImage: put the PNG on the OS clipboard.
	//   - revealStatsImage: reveal a saved PNG in the OS file manager
	//     (`reveal_path_command`).
	saveStatsImage?: (
		dataUrl: string,
		defaultName: string,
		mode: "downloads" | "saveAs",
	) => Promise<{
		success: boolean;
		canceled?: boolean;
		path?: string;
		error?: string;
	}>;
	copyStatsImage?: (dataUrl: string) => Promise<{
		success: boolean;
		error?: string;
	}>;
	revealStatsImage?: (filePath: string) => Promise<{
		success: boolean;
		error?: string;
	}>;
	// Open an https URL in the user's default browser. The
	// replacement for the renderer's bare `window.open(url, "_blank")`
	// / `target="_blank"` anchors, which are blocked or trapped under
	// Tauri (CSP `default-src 'self'`, `plugins.shell.open = false` per
	// C-TAURI-2). The host enforces the SAME https-only policy as
	// the predecessor's `input-nav-guard.ts` (deny rest). Optional: the
	// sandboxed bubble window does not install the namespace.
	openExternalUrl?: (url: string) => Promise<{
		success: boolean;
		error?: string;
	}>;
}
