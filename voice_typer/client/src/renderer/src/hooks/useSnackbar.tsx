// src/renderer/src/hooks/useSnackbar.tsx
//
// NEW-UX-003: previously the renderer had TWO parallel toast systems:
//   1. The bespoke ``useSnackbar`` hook (this file) — used by Settings,
//      Models, Microphone, Vocabulary, Templates, and Onboarding pages.
//   2. The ``sonner`` library — used by History.tsx, ActivityList.tsx,
//      and (partially) Vocabulary.tsx.
//
// Both rendered toasts to the user but looked different, had different
// lifetimes, and stacked badly when both fired at once.  This file now
// delegates ALL toast rendering to ``sonner`` — there is exactly ONE
// toast system in the renderer.  The UI comes from the single global
// ``<Toaster />`` mounted in App.tsx.  Toasts are raised via ``showSnack``
// (or directly via ``toast.success(...)`` from ``sonner``); both go
// through the same renderer.
//
// DX-013: the bespoke ``Snackbar`` component that this hook used to
// return was removed — it was a no-op that made pages render dead
// ``<Snackbar />`` JSX and, once the component stopped being exported,
// crashed those pages at render time.  There is no longer a ``Snackbar``
// member on the returned object; call sites must not destructure or
// render it.  All toast UI is the global sonner ``<Toaster />``.
//
// IMPORTANT: This file is named ``.tsx`` (not ``.ts``) only because of
// historical extension-priority conventions; it no longer contains JSX.
// Vite resolves ``.ts`` before ``.tsx`` in extension priority, so a
// coexisting ``.ts`` file would shadow this one.

import { useCallback } from "react";
import { toast } from "sonner";

export type SnackbarType = "success" | "error" | "warning" | "info";

export interface SnackbarState {
	message: string;
	type: SnackbarType;
}

/**
 * Unified snackbar hook.  All toasts are rendered by sonner's global
 * ``<Toaster />`` mounted in App.tsx.  This hook returns only
 * ``showSnack`` / ``clearSnack`` — there is no ``Snackbar`` component
 * to render (see DX-013).
 *
 * @param timeoutMs Default toast duration in milliseconds.  Sonner's
 *   default is 4000ms; we keep the legacy 3000ms default for parity.
 */
export function useSnackbar(timeoutMs = 3000) {
	const showSnack = useCallback(
		(message: string, type: SnackbarType = "success") => {
			// NEW-UX-003: delegate to sonner so there is exactly ONE toast
			// system in the renderer.  Each ``type`` maps to the matching
			// sonner method so the icon and color come from the global
			// Toaster configuration in ``components/ui/sonner.tsx``.
			const opts = { duration: timeoutMs };
			switch (type) {
				case "success":
					toast.success(message, opts);
					break;
				case "error":
					toast.error(message, opts);
					break;
				case "warning":
					toast.warning(message, opts);
					break;
				case "info":
					toast.info(message, opts);
					break;
			}
		},
		[timeoutMs],
	);

	const clearSnack = useCallback(() => {
		// Dismiss all toasts — sonner doesn't expose per-toast dismissal
		// from this side of the wrapper without an id, so we dismiss all.
		// This matches the previous "clear current snackbar" semantics
		// closely enough for the legacy call sites that use it.
		toast.dismiss();
	}, []);

	return { showSnack, clearSnack };
}

/**
 * NEW-UX-004: helper to show an undoable toast.  Renders a sonner
 * toast with an "Undo" action button.  When the user clicks Undo
 * (or presses the toast's action key), the callback fires.
 *
 * @param message Toast message body.
 * @param undoLabel Label for the action button (default "Undo").
 * @param onUndo Called when the user clicks Undo.
 * @param type Toast type — controls icon + color.
 * @param timeoutMs Duration in ms (default 6000 — longer than a
 *   plain toast so the user has time to click Undo).
 */
export function showUndoableToast(
	message: string,
	onUndo: () => void,
	options: {
		undoLabel?: string;
		type?: SnackbarType;
		timeoutMs?: number;
	} = {},
): void {
	const { undoLabel = "Undo", type = "warning", timeoutMs = 6000 } = options;
	const opts = {
		duration: timeoutMs,
		action: {
			label: undoLabel,
			onClick: onUndo,
		},
	};
	switch (type) {
		case "success":
			toast.success(message, opts);
			break;
		case "error":
			toast.error(message, opts);
			break;
		case "warning":
			toast.warning(message, opts);
			break;
		case "info":
			toast.info(message, opts);
			break;
	}
}
