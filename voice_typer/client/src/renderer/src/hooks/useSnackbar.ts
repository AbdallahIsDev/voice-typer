// src/renderer/src/hooks/useSnackbar.ts
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
// CR-152 (Fix-M) / PVT-9: this file was previously named
// ``useSnackbar.tsx`` but contains no JSX.  The stale duplicate
// ``.tsx`` (left on disk after the rename) was deleted by sessions 1,
// 3, and 5 — only this ``.ts`` file now exists under
// ``@/hooks/useSnackbar``.  Vite/TypeScript resolves imports without
// an extension, so all ``import { useSnackbar } from "@/hooks/useSnackbar"``
// statements continue to work without modification.
//
// PVT-026: toast durations are now standardised per type so that
// transient confirmations disappear quickly while errors stay on
// screen long enough to be read.  Per-call overrides take precedence
// over the per-type defaults — callers that need a custom lifetime
// (e.g. undo toasts) pass ``{ duration: ms }`` as the third argument.
//
//   success → 3000ms  (fleeting "saved" / "copied" pings)
//   info    → 4000ms  (neutral context, e.g. "model loaded")
//   warning → 6000ms  (recoverable issues, undo affordances)
//   error   → 8000ms  (failures the user must act on)

import { useCallback } from "react";
import { toast } from "sonner";

export type SnackbarType = "success" | "error" | "warning" | "info";

export interface SnackbarState {
	message: string;
	type: SnackbarType;
}

/**
 * Per-type default toast durations in milliseconds.  Exported so tests
 * and downstream hooks can reference the canonical values instead of
 * hard-coding magic numbers.
 */
export const SNACKBAR_DEFAULT_DURATION_MS: Record<SnackbarType, number> = {
	success: 3000,
	info: 4000,
	warning: 6000,
	error: 8000,
};

export interface ShowSnackOptions {
	/** Override the per-type default duration (ms). */
	duration?: number;
	/** Optional id — passing the same id replaces the existing toast. */
	id?: string | number;
}

/**
 * Resolve the effective duration for a toast of the given type,
 * honouring an explicit per-call override when present.
 */
function resolveDuration(
	type: SnackbarType,
	options?: ShowSnackOptions,
): number {
	if (options?.duration !== undefined) return options.duration;
	return SNACKBAR_DEFAULT_DURATION_MS[type];
}

/**
 * Unified snackbar hook.  All toasts are rendered by sonner's global
 * ``<Toaster />`` mounted in App.tsx.  This hook returns only
 * ``showSnack`` / ``clearSnack`` — there is no ``Snackbar`` component
 * to render (see DX-013).
 */
export function useSnackbar() {
	const showSnack = useCallback(
		(
			message: string,
			type: SnackbarType = "success",
			options?: ShowSnackOptions,
		) => {
			// NEW-UX-003: delegate to sonner so there is exactly ONE toast
			// system in the renderer.  Each ``type`` maps to the matching
			// sonner method so the icon and color come from the global
			// Toaster configuration in ``components/ui/sonner.tsx``.
			// PVT-026: per-type default durations with per-call override.
			const opts = {
				duration: resolveDuration(type, options),
				...(options?.id !== undefined ? { id: options.id } : {}),
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
		},
		[],
	);

	const clearSnack = useCallback((id?: string | number) => {
		// Dismiss a specific toast when an id is given; otherwise
		// dismiss all active toasts. The optional id maps 1:1 to
		// ``toast.dismiss(id)`` so callers that have shown a
		// context-specific toast can dismiss just that one without
		// killing unrelated toasts. Omitting id preserves the
		// legacy "clear all" semantics for existing call sites.
		if (id !== undefined) {
			toast.dismiss(id);
		} else {
			toast.dismiss();
		}
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
 * @param timeoutMs Duration in ms.  Defaults to the per-type default
 *   (6000ms for warnings) — callers that need a longer window (e.g.
 *   destructive undos) pass an explicit ``timeoutMs``.
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
	const { undoLabel = "Undo", type = "warning", timeoutMs } = options;
	const duration = timeoutMs ?? SNACKBAR_DEFAULT_DURATION_MS[type];
	const opts = {
		duration,
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
