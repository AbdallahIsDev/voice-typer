// src/renderer/src/hooks/useSnackbar.tsx
//
// NEW-UX-003: previously the renderer had TWO parallel toast systems:
//   1. The bespoke ``useSnackbar`` hook (this file) — used by 6 pages
//      (Settings, Models, Microphone, Vocabulary, Templates, Onboarding).
//   2. The ``sonner`` library — used by History.tsx, ActivityList.tsx,
//      and (partially) Vocabulary.tsx.
//
// Both rendered toasts to the user but looked different, had different
// lifetimes, and stacked badly when both fired at once.  This file now
// delegates ALL toast rendering to ``sonner`` — the bespoke ``Snackbar``
// component returned by this hook is a no-op so existing call sites
// continue to compile and work without modification, while the actual
// UI comes from the single global ``<Toaster />`` mounted in App.tsx.
//
// Migration path: existing call sites can keep using ``showSnack`` /
// ``useSnackbar``.  New code may call ``toast.success(...)`` directly
// from ``sonner`` — both go through the same renderer.
//
// IMPORTANT: This file MUST be .tsx (not .ts) because it contains JSX
// for the Snackbar component.  Vite resolves .ts before .tsx in
// extension priority, so a coexisting .ts file would shadow this one.

import { useCallback } from "react";
import { toast } from "sonner";

export type SnackbarType = "success" | "error" | "warning" | "info";

export interface SnackbarState {
	message: string;
	type: SnackbarType;
}

/**
 * Unified snackbar hook.  All toasts are rendered by sonner's global
 * ``<Toaster />``.  The returned ``Snackbar`` component is a no-op
 * (returns null) — kept for backwards compatibility with pages that
 * still render ``<Snackbar />`` in their JSX.
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

	/**
	 * No-op renderer.  Sonner renders toasts via its own portal, so we
	 * don't need an inline component.  Kept for backwards compatibility
	 * with pages that render ``<Snackbar />`` in their JSX.
	 */
	const Snackbar = useCallback(() => null, []);

	return { snackbar: null, showSnack, clearSnack, Snackbar };
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
