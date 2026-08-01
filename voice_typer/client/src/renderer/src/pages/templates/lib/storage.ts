// Backend + localStorage persistence for templates.
//
// Extracted from the former ``pages/Templates.tsx`` module-level helpers
// (loadTemplatesFromLocalStorage, loadTemplatesFromBackend, saveTemplates,
// makeRowId).  The page hook ``useTemplates`` is the only consumer of
// these helpers, but isolating them in a pure-data module keeps the hook
// readable and lets unit tests target the storage layer without rendering
// React.

import { sanitizeTemplateField } from "./sanitize";
import type { Template } from "./types";

//Templates are persisted by the Python backend to
// ``voice-typer-templates.json`` in the user's voice-typer config
// directory (``~/.voice-typer`` on POSIX, ``%APPDATA%\voice-typer``
// on Windows).  This file survives Electron userData resets and
// reinstalls, so templates are no longer lost on app data wipe.
//
// localStorage is now used ONLY as a one-time migration source: if
// the backend has no templates but localStorage does (e.g. user
// upgrades from a previous build), we push the localStorage data to
// the backend on first load and then localStorage is no longer read.
export const STORAGE_KEY = "templates_data";
export const MIGRATION_FLAG_KEY = "templates_migrated_to_backend";

export function loadTemplatesFromLocalStorage(): Template[] {
	try {
		const raw = localStorage.getItem(STORAGE_KEY) ?? "[]";
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		// SEC-027: sanitize each template field on load. localStorage is a
		// stored-XSS vector IF any future code path renders a template value
		// via dangerouslySetInnerHTML. We strip angle brackets and null
		// bytes from trigger + output so even a malicious payload injected
		// into localStorage (by another process, a browser extension, or a
		// prior compromised session) cannot contain HTML markup. Plain text
		// templates are unaffected. The variables list still scans the
		// sanitized output for {today}/{now}/{clipboard}/{username}.
		return parsed.map((t: Partial<Template>) => ({
			trigger: sanitizeTemplateField(t.trigger),
			output: sanitizeTemplateField(t.output),
			match_mode: t.match_mode === "contains" ? "contains" : "exact",
		}));
	} catch {
		return [];
	}
}

/**
 * : load templates from the Python backend.  Falls back to
 * localStorage on IPC failure (e.g. backend not yet started) so the
 * page remains usable during startup.
 *
 * : previously this function returned `[]` for BOTH "no
 * templates exist" (valid empty array from backend) AND "the backend
 * returned malformed data" (null/undefined result, or a `templates`
 * field that wasn't an array). That collapsed two very different
 * states into one empty list, hiding genuine load failures from the
 * user. Now we throw on genuine failure and only return `[]` when the
 * backend explicitly reported an empty (but valid) template list.
 */
export async function loadTemplatesFromBackend(
	callFn: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<Template[]> {
	const result = await callFn<{ templates?: Template[] } | Template[]>(
		"get_templates",
	);
	// The IPC layer may return either { templates: [...] } or a bare
	// array — accept both for forward/backward compat.
	const arr = Array.isArray(result) ? result : result?.templates;
	if (!Array.isArray(arr)) {
		// Genuine failure: the backend returned a non-array shape (null,
		// undefined, or a malformed object). Distinguish from a valid
		// empty list (arr === []) so the caller can surface a load error
		// instead of treating this as "no templates exist".
		throw new Error(
			"Backend returned malformed templates payload (expected array)",
		);
	}
	return arr.map((t: Partial<Template>) => ({
		trigger: sanitizeTemplateField(t.trigger),
		output: sanitizeTemplateField(t.output),
		match_mode: t.match_mode === "contains" ? "contains" : "exact",
	}));
}

// #6: saveTemplates now accepts an optional callFn for IPC persistence.
// Add/edit paths pass the IPC call function so the server is notified.
// Delete path also passes callFn so the server stays in sync.
//
//backend persistence is now functional (previously the
// IPC save was a no-op because the Config dataclass had no
// templates_data field).  We still mirror to localStorage as a
// startup-fallback cache in case the backend is unreachable on next
// launch (e.g. user opens the page during Python boot).
//
//now async so callers can `await saveTemplates(...)` before
// triggering `loadRows()`.  Previously the IPC save was fire-and-forget
// (`.catch(...)`), which meant `loadRows()` could re-read the backend
// BEFORE the save landed — racing the just-saved list out of the UI
// and re-rendering the pre-save state.  Awaiting guarantees the load
// sees the new state.
//
//the IPC error path previously swallowed the rejection after
// logging it — callers had no way to know the save failed, so they
// showed a success toast even when the backend rejected the write.
// We now rethrow after logging so the calling hook (e.g.
// useTemplateDialog.saveTemplate, useTemplates.instantDeleteTemplate,
// useTemplateImportExport.handleImportFile) can catch the rejection
// and surface an error toast instead of (or in addition to) the
// success toast.
export async function saveTemplates(
	items: Template[],
	callFn?: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<void> {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
	} catch (e) {
		// localStorage may be unavailable (private mode, quota exceeded).
		// The backend is the source of truth now, so this is non-fatal.
		console.warn(
			"[templates/storage] saveTemplates localStorage.setItem failed:",
			e,
		);
	}
	if (callFn) {
		try {
			await callFn("save_templates", { templates: items });
		} catch (err: unknown) {
			//log the IPC failure for diagnostics, then rethrow so
			// the caller can show an error toast instead of the success
			// toast it likely already queued (the success toast is fired
			// before the await in some callers — see useTemplateDialog).
			console.error("IPC save_templates failed:", err);
			throw err;
		}
	}
}

/**
 * Generate a stable UUID for a row.  Uses the Web Crypto API
 * (`crypto.randomUUID`) which is available in Electron's renderer
 * (Chromium) and in jsdom (Node ≥ 19).  Falls back to a
 * `Math.random`-based pseudo-ID if `crypto.randomUUID` is unavailable
 * (older runtimes / sandboxed tests) so the React key is still unique
 * within the session — UUID quality doesn't matter here because the
 * ID is never persisted, only used as a React key.
 */
export function makeRowId(): string {
	try {
		if (
			typeof crypto !== "undefined" &&
			typeof crypto.randomUUID === "function"
		) {
			return crypto.randomUUID();
		}
	} catch (e) {
		// crypto may be undefined in some test environments.
		// Fall through to the Math.random-based pseudo-ID below.
		console.warn(
			"[templates/storage] crypto.randomUUID unavailable, falling back:",
			e,
		);
	}
	return `row-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}
