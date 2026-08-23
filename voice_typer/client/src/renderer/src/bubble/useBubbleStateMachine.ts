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
 *   - `transcript` is an optional short partial-transcription string
 *     surfaced from the same `bubble:set-state` payload when in
 *     transcribing mode. The main-process handler at
 *     `main/python/handle-message.ts` forwards the full payload
 *     (state + optional `message` + optional `transcript`) whenever the
 *     backend emits those fields, and the bare state string otherwise —
 *     the parsing is forward-compatible with both shapes, so once the
 *     backend pushes `{ state: "transcribing", transcript: "..." }`,
 *     the renderer will display the live partial text in the bubble
 *     pill (XA-6-2). No IPC surface change is required on the renderer
 *     side — the existing `bubble:set-state` channel already supports
 *     the richer payload shape.
 *
 * Subscribes to the bridge's `show` / `hide` / `setState` events.
 */
import {
	type Dispatch,
	type SetStateAction,
	useEffect,
	useRef,
	useState,
} from "react";
import {
	type AnimState,
	type BubbleMode,
	nextBubbleMode,
	parseSetStatePayload,
} from "./constants";
import { useBubbleBridge } from "./useBubbleBridge";

export interface BubbleStateMachine {
	mode: BubbleMode;
	animState: AnimState;
	setAnimState: Dispatch<SetStateAction<AnimState>>;
	exitTick: number;
	setExitTick: Dispatch<SetStateAction<number>>;
	/** Short reason string for the current error mode, or `null`. */
	errorMessage: string | null;
	/**
	 * Short partial-transcription string for the current transcribing
	 * (or fading) mode, or `null` when no transcript has been pushed
	 * yet. Cleared on transition to a non-transcribing mode.
	 */
	transcript: string | null;
	/**
	 * True when the backend signalled that the active engine cannot
	 * stream live partials (no `transcribe_words` — Parakeet/Qwen).
	 * Sticky while recording stays the current mode; cleared on any
	 * transition to another mode so a later Whisper recording starts
	 * clean.
	 */
	livePreviewUnsupported: boolean;
}

export function useBubbleStateMachine(): BubbleStateMachine {
	const bridge = useBubbleBridge();
	const [mode, setMode] = useState<BubbleMode>("recording");
	const [animState, setAnimState] = useState<AnimState>("enter");
	const [exitTick, setExitTick] = useState(0);
	const [errorMessage, setErrorMessage] = useState<string | null>(null);
	// Live partial-transcription text (XA-6-2). Populated from the
	// `transcript` field of the `bubble:set-state` payload when in
	// transcribing mode; preserved across the transcribing → fading
	// transition so the partial text fades out smoothly with the pill.
	// Cleared on transition to any other mode.
	const [transcript, setTranscript] = useState<string | null>(null);
	// Engine-capability signal from the backend's one-time
	// live-preview publish: true = the active engine cannot stream
	// partials. Kept while recording continues (legacy bare payloads
	// carry no opinion); cleared on leaving recording so the next
	// session with a different engine starts clean.
	const [livePreviewUnsupported, setLivePreviewUnsupported] = useState(false);

	// Latest-mode ref so the `onSetState` callback can read the current
	// mode synchronously without re-subscribing on every mode change
	// (which would cancel + re-arm the rAF loop in `useAudioLevels`,
	// causing visible stutter). Updated inline on every render — this
	// is the same pattern `useAudioLevels` uses for `visibleRef`.
	const modeRef = useRef<BubbleMode>("recording");
	modeRef.current = mode;

	useEffect(() => {
		if (!bridge) return;

		const offShow = bridge.on("show", () => {
			setExitTick(0); // Cancel any pending exit
			setAnimState("enter");
			// Don't override transcribing/fading mode if a state change
			// arrived before our show() event. This prevents a race
			// where the backend calls set_state("transcribing") and
			// then show() is re-triggered. The transition table is the
			// shared `nextBubbleMode` reducer (IN-62 single source of
			// truth) — the bridge's authoritative mode ref applies the
			// same function to the same event, so the two stay in
			// lockstep by construction.
			setMode((prev) => nextBubbleMode(prev, { type: "show" }));
		});

		const offHide = bridge.on("hide", () => {
			// Two-stage transition when leaving transcribing state:
			// first fade the transcribing content out smoothly, then
			// trigger the bubble exit animation.
			setMode((prev) => nextBubbleMode(prev, { type: "hide" }));
			setExitTick((t) => t + 1);
		});

		return () => {
			offShow();
			offHide();
		};
	}, [bridge]);

	// Listen for state changes from the Python backend. When recording
	// stops, Python sends "transcribing" so the bubble hides the
	// visualizer and shows "Transcribing..." text. When transcription
	// completes, it sends "idle" (for always_visible mode) or hide().
	// "error" surfaces a red ⚠ Error label (with an optional reason
	// string when the backend + main process forward one).
	useEffect(() => {
		if (!bridge) return;

		const off = bridge.on("setState", (stateArg) => {
			const {
				state,
				message,
				transcript: newTranscript,
				livePreviewSupported,
			} = parseSetStatePayload(stateArg);

			// Engine-capability hint (live preview unavailable). An
			// explicit boolean updates the flag; an absent field keeps
			// the previous value so legacy bare payloads and the
			// per-partial mirrors don't clear the hint mid-recording.
			// Preserved across fading; cleared on every other mode.
			if (state === "fading") {
				// no-op: preserve across the fade-out transition.
			} else if (livePreviewSupported !== null) {
				setLivePreviewUnsupported(livePreviewSupported === false);
			} else if (state !== "recording") {
				setLivePreviewUnsupported(false);
			}

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
				setTranscript(null);
				return;
			}

			setMode((prev) => nextBubbleMode(prev, { type: "setState", state }));

			// Surface / clear the error reason string. Only update
			// `errorMessage` when entering error mode (or when leaving
			// it for a non-fading state) so a stale message doesn't
			// persist after the user retries.
			if (state === "error") {
				setErrorMessage(message);
			} else if (state !== "fading") {
				setErrorMessage(null);
			}

			// Surface / clear the live partial-transcript text
			// (XA-6-2 + live streaming partials). Update `transcript`
			// whenever the new payload carries one — during BOTH the
			// transcribing mode (finalize-time text) and the recording
			// mode (mid-recording live partials mirrored onto the
			// bubble channel), including an empty string → null mapping
			// so the pill can clear mid-flow. Preserve the previous
			// value across the fading transition so the partial text
			// fades out smoothly with the pill instead of vanishing
			// a frame before the exit animation kicks in.
			if (state === "transcribing" || state === "recording") {
				setTranscript(newTranscript);
			} else if (state === "fading") {
				// No-op: preserve the existing transcript so the
				// fade-out renders the last partial text.
			} else {
				setTranscript(null);
			}
		});
		return off;
	}, [bridge]);

	return {
		mode,
		animState,
		setAnimState,
		exitTick,
		setExitTick,
		errorMessage,
		transcript,
		livePreviewUnsupported,
	};
}
