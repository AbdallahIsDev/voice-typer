/**
 * Bubble overlay package — `useBubbleStateMachine` hook.
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16).
 *
 * Owns the bubble's mode/animation state machine.
 *
 *   - `mode` is one of `"recording" | "transcribing" | "idle" |
 *     "fading" | "error"`. The first four mirror the original Bubble
 *     behavior; `"error"` (PVT fix) is set when the backend pushes
 *     `set_state("error")` so the overlay can surface a red "⚠ Error"
 *     label instead of silently keeping the last mode.
 *   - `animState` is `"enter" | "exit" | ""` and drives the CSS
 *     `animate-bubble-enter` / `animate-bubble-exit` classes.
 *   - `exitTick` is incremented on each hide request to force the
 *     exit effect (in `Bubble.tsx`) to re-run even when mode doesn't
 *     change (e.g. recording → recording).
 *
 * Subscribes to `api.onShow`, `api.onHide`, and `api.onSetState`.
 */
import { type Dispatch, type SetStateAction, useEffect, useState } from "react";
import type { AnimState, BubbleMode } from "./constants";

export interface BubbleStateMachine {
	mode: BubbleMode;
	setMode: Dispatch<SetStateAction<BubbleMode>>;
	animState: AnimState;
	setAnimState: Dispatch<SetStateAction<AnimState>>;
	exitTick: number;
	setExitTick: Dispatch<SetStateAction<number>>;
}

export function useBubbleStateMachine(): BubbleStateMachine {
	const [mode, setMode] = useState<BubbleMode>("recording");
	const [animState, setAnimState] = useState<AnimState>("enter");
	const [exitTick, setExitTick] = useState(0);

	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api) return;

		const offShow = api.onShow(() => {
			setExitTick(0); // Cancel any pending exit
			setAnimState("enter");
			// BUBBLE-FIX: don't override transcribing/fading mode if a
			// state change arrived before our show() event. This prevents
			// a race where the backend calls set_state("transcribing")
			// and then show() is re-triggered.
			setMode((prev) => {
				if (prev === "transcribing") return prev;
				return "recording";
			});
		});

		const offHide = api.onHide(() => {
			// Two-stage transition when leaving transcribing state:
			// first fade the transcribing content out smoothly, then
			// trigger the bubble exit animation.
			setMode((prev) => (prev === "transcribing" ? "fading" : prev));
			setExitTick((t) => t + 1);
		});

		return () => {
			offShow();
			offHide();
		};
	}, []);

	// Listen for state changes from the Python backend. When recording
	// stops, Python sends "transcribing" so the bubble hides the
	// visualizer and shows "Transcribing..." text. When transcription
	// completes, it sends "idle" (for always_visible mode) or hide().
	// "error" surfaces a red ⚠ Error label (PVT fix).
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onSetState) return;

		const off = api.onSetState((state) => {
			setMode((prev) => {
				// Ignore state changes while fading out (exit in progress)
				if (prev === "fading") return prev;

				if (state === "transcribing") return "transcribing";
				if (state === "idle") return "idle";
				if (state === "recording") return "recording";
				if (state === "error") return "error";
				return prev;
			});
		});
		return off;
	}, []);

	return { mode, setMode, animState, setAnimState, exitTick, setExitTick };
}
