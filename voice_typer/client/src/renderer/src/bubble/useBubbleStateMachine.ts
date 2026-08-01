/**
 * Bubble overlay package — `useBubbleStateMachine` hook.
 *
 * Owns the bubble's mode/animation state machine.
 *
 *   - `mode` is one of `"recording" | "transcribing" | "idle" |
 *     "fading" | "error"`. The first four mirror the original Bubble
 *     behavior; `"error"` is set when the backend pushes
 *     `set_state("error")` so the overlay can surface a red "⚠ Error"
 *     label (and an optional short reason string) instead of silently
 *     keeping the last mode.
 *   - `animState` is `"enter" | "exit" | ""` and drives the CSS
 *     `animate-bubble-enter` / `animate-bubble-exit` classes.
 *   - `exitTick` is incremented on each hide request to force the
 *     exit effect (in `Bubble.tsx`) to re-run even when mode doesn't
 *     change (e.g. recording → recording).
 *   - `errorMessage` is an optional short reason string surfaced from
 *     the `bubble:set-state` payload when entering error mode. The
 *     `onSetState` IPC callback is typed as `(state: string) => void`,
 *     but the runtime payload MAY be a richer object
 *     `{ state: string; message?: string }` if/when the backend + main
 *     process are extended to forward error reasons. The defensive
 *     runtime check below handles both shapes so this hook is
 *     forward-compatible without a type-system change to
 *     `BubbleWindowExtras.onSetState` (owned by another sub-agent).
 *
 * Subscribes to `api.onShow`, `api.onHide`, and `api.onSetState`.
 */
import {
	type Dispatch,
	type SetStateAction,
	useEffect,
	useRef,
	useState,
} from "react";
import type { AnimState, BubbleMode } from "./constants";

export interface BubbleStateMachine {
	mode: BubbleMode;
	setMode: Dispatch<SetStateAction<BubbleMode>>;
	animState: AnimState;
	setAnimState: Dispatch<SetStateAction<AnimState>>;
	exitTick: number;
	setExitTick: Dispatch<SetStateAction<number>>;
	/** Short reason string for the current error mode, or `null`. */
	errorMessage: string | null;
}

/**
 * Normalise the `bubble:set-state` payload into a state string + an
 * optional message. The IPC type is `(state: string) => void`, but the
 * runtime payload MAY be a richer object once the backend + main
 * process are extended to forward error reasons. Defensive duck-typing
 * keeps this hook forward-compatible without requiring a type-system
 * change to `BubbleWindowExtras.onSetState`.
 */
function parseSetStatePayload(arg: unknown): {
	state: string;
	message: string | null;
} {
	if (typeof arg === "string") {
		return { state: arg, message: null };
	}
	if (arg && typeof arg === "object" && "state" in arg) {
		const obj = arg as { state: unknown; message?: unknown };
		const stateStr =
			typeof obj.state === "string" ? obj.state : String(obj.state);
		const message =
			typeof obj.message === "string" && obj.message.length > 0
				? obj.message
				: null;
		return { state: stateStr, message };
	}
	return { state: String(arg), message: null };
}

export function useBubbleStateMachine(): BubbleStateMachine {
	const [mode, setMode] = useState<BubbleMode>("recording");
	const [animState, setAnimState] = useState<AnimState>("enter");
	const [exitTick, setExitTick] = useState(0);
	const [errorMessage, setErrorMessage] = useState<string | null>(null);

	// Latest-mode ref so the `onSetState` callback can read the current
	// mode synchronously without re-subscribing on every mode change
	// (which would cancel + re-arm the rAF loop in `useAudioLevels`,
	// causing visible stutter). Updated inline on every render — this
	// is the same pattern `useAudioLevels` uses for `visibleRef`.
	const modeRef = useRef<BubbleMode>("recording");
	modeRef.current = mode;

	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api) return;

		const offShow = api.onShow(() => {
			setExitTick(0); // Cancel any pending exit
			setAnimState("enter");
			// Don't override transcribing/fading mode if a state change
			// arrived before our show() event. This prevents a race
			// where the backend calls set_state("transcribing") and
			// then show() is re-triggered.
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
	// "error" surfaces a red ⚠ Error label (with an optional reason
	// string when the backend + main process forward one).
	useEffect(() => {
		const api = window.bubble as
			| import("@/types/ipc").BubbleWindowBubble
			| undefined;
		if (!api?.onSetState) return;

		const off = api.onSetState((stateArg) => {
			const { state, message } = parseSetStatePayload(stateArg);

			// Recording interrupts the fading→exit transition (e.g.
			// user starts a new dictation while the previous
			// transcribing pill is still fading out). Without this
			// override, the new recording's `set_state("recording")`
			// would be silently ignored — the `prev === "fading"`
			// guard below ate it — and the bubble would keep exiting,
			// leaving the user with no visible recording indicator.
			// Mirrors the `onShow` handler's exit-cancel logic: zero
			// exitTick so `Bubble.tsx`'s fadeOutTimer effect doesn't
			// fire `setAnimState("exit")`, restore the enter animation
			// so the bubble pops back in, and switch mode to recording.
			if (state === "recording" && modeRef.current === "fading") {
				setExitTick(0);
				setAnimState("enter");
				setMode("recording");
				setErrorMessage(null);
				return;
			}

			setMode((prev) => {
				// Ignore non-recording state changes while fading out
				// (exit in progress) — the recording-interrupt case is
				// handled above.
				if (prev === "fading") return prev;

				if (state === "transcribing") return "transcribing";
				if (state === "idle") return "idle";
				if (state === "recording") return "recording";
				if (state === "error") return "error";
				if (state === "blocked") return "blocked";
				if (state === "cancelling") return "cancelling";
				if (state === "permission_revoked") return "permission_revoked";
				if (state === "paste_failed") return "paste_failed";
				return prev;
			});

			// Surface / clear the error reason string. Only update
			// `errorMessage` when entering error mode (or when leaving
			// it for a non-fading state) so a stale message doesn't
			// persist after the user retries.
			if (state === "error") {
				setErrorMessage(message);
			} else if (state !== "fading") {
				setErrorMessage(null);
			}
		});
		return off;
	}, []);

	return {
		mode,
		setMode,
		animState,
		setAnimState,
		exitTick,
		setExitTick,
		errorMessage,
	};
}
