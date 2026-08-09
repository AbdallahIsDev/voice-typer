// types/ipc/bridge.ts
//
// The two preload-exposed bridge interfaces: `PythonBridge` (the
// `window.python` API surface) and `WindowBridge` (the
// `window.window_` API surface for the custom title bar + GDPR export
// helpers + native pickers).
//
//Split out from the original monolithic `types/ipc.ts` ( / ).
// No behaviour change vs. the original file — pure structural refactor.
//
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
	//forward a renderer-caught error (e.g. from React's
	// `componentDidCatch`) to the main process for persistence in
	// `electron-renderer-errors.log`. The sandboxed renderer can't
	// write to userData directly — only the main process can.
	// Optional so the Tauri bridge (which has no main-process file
	// system access) can omit it without breaking the type contract.
	logError?: (payload: {
		kind: string;
		stack?: string;
		componentStack?: string;
		message?: string;
	}) => Promise<void>;
	//native folder picker for HuggingFace model imports. Was
	// missing from the type — Models.tsx accessed it via a runtime cast.
	// Declared optional because the Tauri bridge installs it but the
	// legacy Electron preload also installs it (so the type is satisfied
	// on both paths).
	openModelImportDialog?: () => Promise<{
		canceled: boolean;
		path?: string;
		error?: string;
	}>;
	// Push the renderer's current locale to the main process so it can
	// localise native dialogs (single-instance error, critical-error
	// dialog, model-folder picker, export save-as dialogs). Registered
	// in `main/ipc/window-handlers.ts` as the `i18n:set-locale` IPC
	// handler. Optional because the Tauri bridge does not currently
	// install it (Tauri-side dialogs are localised via the OS locale,
	// not a renderer-pushed value).
	setLocale?: (locale: string) => Promise<unknown>;
	//Restart the Python backend process only (Electron stays alive).
	// Used by the "Lost connection" Retry escalation AFTER a plain
	// reconnect probe fails. Optional because the Tauri bridge has no
	// main-process spawn surface (Tauri's Rust host owns the backend
	// lifecycle there). Electron preload always installs it.
	restartBackend?: () => Promise<{
		ok: boolean;
		reason?: string;
	}>;
}
