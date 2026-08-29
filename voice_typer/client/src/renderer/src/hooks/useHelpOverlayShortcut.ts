/**
 * useHelpOverlayShortcut — "?" key opens the help overlay, Escape
 * closes it.
 *
 * Extracted from App.tsx (Phase 4.5 spaghetti split) to keep
 * App.tsx a pure layout shell. Behaviour is byte-identical to the
 * original inline effect:
 *
 *   - ``?`` (no ctrl/meta/alt) opens the overlay, UNLESS focus is in
 *     an editable control (input/textarea/select/contentEditable) or
 *     a Radix dialog/modal is open (``[role="dialog"][data-state="open"]``).
 *   - ``Escape`` while open closes it, stopping propagation so Radix
 *     Modal's own Escape handler doesn't race it.
 *
 * Returns the open flag plus stable open/close callbacks so the caller
 * can wire the overlay and any other triggers (e.g. TitleBar help
 * button) without re-creating callbacks each render.
 */
import { useCallback, useEffect, useState } from "react";

/**
 * Wire the "?" / Escape help-overlay keyboard shortcut.
 *
 * @returns ``{ showHelpOverlay, openHelp, closeHelp }`` — the open
 *   flag plus stable callbacks (memoized with empty deps) suitable for
 *   passing to React.memo'd children.
 */
export function useHelpOverlayShortcut(): {
	showHelpOverlay: boolean;
	openHelp: () => void;
	closeHelp: () => void;
} {
	const [showHelpOverlay, setShowHelpOverlay] = useState(false);

	useEffect(() => {
		const handler = (e: KeyboardEvent) => {
			if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey) {
				const active = document.activeElement as HTMLElement | null;
				const tag = active?.tagName?.toLowerCase();
				if (
					tag === "input" ||
					tag === "textarea" ||
					tag === "select" ||
					active?.isContentEditable === true
				)
					return;
				// If any Radix Dialog-based modal is currently
				// open (ConfirmDialog, AlertDialog, the help overlay
				// itself, etc.), don't pop the help overlay on top of
				// it. Radix renders dialog content via Portal into
				// document.body with role="dialog" + data-state="open",
				// so a single querySelector covers every Modal/AlertDialog
				// instance in the app.
				if (document.querySelector('[role="dialog"][data-state="open"]'))
					return;
				e.preventDefault();
				setShowHelpOverlay(true);
			} else if (e.key === "Escape" && showHelpOverlay) {
				// Stop the event from bubbling into Radix Modal's own
				// Escape handler (and from triggering browser-default
				// Exit Fullscreen / cancel actions on the page) so the
				// help overlay closes deterministically without racing
				// another Escape consumer.
				e.preventDefault();
				e.stopPropagation();
				setShowHelpOverlay(false);
			}
		};
		document.addEventListener("keydown", handler);
		return () => document.removeEventListener("keydown", handler);
	}, [showHelpOverlay]);

	const openHelp = useCallback(() => setShowHelpOverlay(true), []);
	const closeHelp = useCallback(() => setShowHelpOverlay(false), []);

	return { showHelpOverlay, openHelp, closeHelp };
}
