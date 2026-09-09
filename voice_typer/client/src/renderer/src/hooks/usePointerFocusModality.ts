// Pointer-modality focus tracking — the SINGLE shared implementation of
// the C-FOCUS-3 contract, consumed by the shared text-input primitives
// (`components/ui/input.tsx` and `components/ui/textarea.tsx`).
//
// WHY THIS EXISTS: browsers match `:focus-visible` for TEXT BOXES on
// BOTH click and keyboard (MDN :focus-visible: "when a text box needing
// user input has focus, focus is indicated"), so the full-opacity
// `focus-visible:ring-ring` ring (the WCAG 1.4.11 3:1 contract pinned by
// `focus-ring-contrast.test.tsx`) paints on every mouse click into a
// text field. Pure CSS cannot separate mouse from keyboard on text
// inputs — the modality must be tracked in JS:
//
//   • a `pointerdown` sets pointer-modality → the heavy ring is
//     suppressed (the caret already marks the field active) and a
//     subtle border tint takes its place;
//   • a Tab / Arrow `keydown` switches back to keyboard modality →
//     keyboard/AT focus keeps the clear full-opacity ring (WCAG
//     2.4.7 "Focus Visible" — the indicator must stay for keyboard
//     users; only the pointer path is suppressed);
//   • `blur` resets the modality so the next keyboard focus gets the
//     full ring even after a previous pointer interaction.
//
// The class pair below is shared so Input and Textarea render the exact
// same suppression/keyboard classes (previously duplicated verbatim in
// both files — a future contract fix would have had to land twice).
//
// OVERRIDE SEMANTICS (C-FOCUS-4-adjacent): consumers spread
// `pointerFocusProps` BEFORE `{...props}`, so a caller-supplied
// `onPointerDown`/`onKeyDown`/`onBlur` REPLACES the modality handler
// (the same clobber contract SearchField documents). Callers must NOT
// pass these three handlers unless they deliberately opt out of the
// suppression — see `components/common/SearchField.tsx`.
import type * as React from "react";
import { useState } from "react";

/** Modality class pair — one source of truth for both primitives. */
export const POINTER_FOCUS_CLASSES = {
	/** Pointer focus: no heavy ring (the caret marks the active field);
	 * a subtle border tint keeps the state legible. */
	pointer: "focus:border-ring/60 focus-visible:ring-0",
	/** Keyboard/AT focus: the clear full-opacity ring (WCAG 1.4.11 3:1). */
	keyboard:
		"focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring",
} as const;

/** The handler trio the consumer spreads onto its <input>/<textarea>. */
export interface PointerFocusProps {
	onPointerDown: () => void;
	onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => void;
	onBlur: () => void;
}

/**
 * Tracks whether the current focus session was entered via a pointing
 * device. Returns the modality flag plus the event handlers that drive
 * the state machine — spread `pointerFocusProps` on the field element
 * BEFORE the caller `{...props}` spread (see override semantics above)
 * and branch the focus classes on `pointerActive`.
 */
export function usePointerFocusModality(): {
	pointerActive: boolean;
	pointerFocusProps: PointerFocusProps;
} {
	const [pointerActive, setPointerActive] = useState(false);

	return {
		pointerActive,
		pointerFocusProps: {
			onPointerDown: () => setPointerActive(true),
			// Only navigation keys switch back to keyboard modality —
			// ordinary typing (e.g. "a", Enter) is NOT a modality signal.
			onKeyDown: (event) => {
				if (event.key === "Tab" || event.key.startsWith("Arrow")) {
					setPointerActive(false);
				}
			},
			onBlur: () => setPointerActive(false),
		},
	};
}
