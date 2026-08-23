/**
 * Bubble overlay package — shared constants.
 *
 * All visualisation tuning knobs and the shared button `className`
 * live here so the three button components (`BubbleMicButton`,
 * `BubbleStopButton`, `BubbleDismissButton`) stay in sync by
 * construction rather than by copy/paste.
 */

// ── Types ────────────────────────────────────────────────────────────

export type BubbleMode =
	| "recording"
	| "transcribing"
	| "idle"
	| "fading"
	| "error"
	| "blocked"
	| "cancelling"
	| "permission_revoked"
	| "paste_failed";

export type AnimState = "enter" | "exit" | "";

export type BubbleAction = "mic" | "dismiss";

// ── Visualizer constants ─────────────────────────────────────────────

export const DOT_COUNT = 7;
export const MIN_HEIGHT = 5;
// Reduced from 32 → 22 to fit inside the h-6 (24px) wrapper with 2px
// vertical headroom.
export const MAX_HEIGHT = 22;

/** Per-bar response weights — gentle bell so the spectrum looks organic. */
export const DOT_WEIGHTS = [0.5, 0.75, 1.0, 0.95, 1.0, 0.75, 0.5];

/**
 * Pre-computed `[0, 1, … DOT_COUNT-1]` index array. Previously
 * `BubbleVisualizer` allocated a fresh `Array.from({ length: DOT_COUNT },
 * (_, i) => i)` on every render — small but unnecessary garbage. Hoisted
 * to module scope so the JSX `.map` uses a stable reference.
 */
export const DOT_INDICES: readonly number[] = Array.from(
	{ length: DOT_COUNT },
	(_, i) => i,
);

/** Transcribing dots animation count. */
export const TRANSCRIBING_DOT_COUNT = 3;

/** Duration (ms) for the transcribing content fade-out before bubble exits. */
export const FADEOUT_DURATION_MS = 150;

// ── Shared button className ──────────────────────────────────────────

/**
 * The shared Tailwind `className` for `BubbleMicButton`,
 * `BubbleStopButton`, and `BubbleDismissButton`. The three affordances
 * must render pixel-identically (same sizing, same hover palette, same
 * `no-drag` opt-out so clicks bubble through the Electron
 * `-webkit-app-region: drag` region) so they read as siblings of one
 * pill.
 *
 * Uses semantic tokens (`text-muted-foreground`, `bg-muted`,
 * `text-foreground`, `ring-ring`) so the buttons inherit the active
 * theme preset's palette instead of hardcoded `zinc-*` colors. The
 * `dark:hover:bg-muted/50` variant softens the hover surface in dark
 * mode (where `--muted` is already a dark surface) — full-strength
 * `bg-muted` on hover would feel too aggressive in dark themes.
 *
 * `focus-visible:ring-2 focus-visible:ring-ring` ensures the buttons
 * have a visible focus indicator for keyboard / AT users navigating
 * via screen-reader cursor. Note: the bubble BrowserWindow is created
 * with `focusable: false` (see `main/windows/bubble-window.ts`), so
 * this ring never actually renders in the shipped app — but it's
 * correct a11y hygiene and would matter immediately if the
 * `focusable` flag is ever flipped (see the keyboard-accessibility
 * trade-off notes in `Bubble.tsx`).
 *
 * `ms-1` (margin-inline-start) replaces the original `ml-1` for RTL
 * safety: in LTR it renders as margin-left; in RTL (ar locale) it
 * flips to margin-right automatically.
 */
export const BUBBLE_BUTTON_CLASS =
	"no-drag ms-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-(--text-muted) transition-colors hover:bg-(--surface-hover) hover:text-(--text-primary) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0";

// ── Mode transition (IN-62 single source of truth) ───────────────────

/**
 * Pure bubble-mode reducer — the SINGLE implementation of every mode
 * transition in the bubble package (IN-62).
 *
 * Pre-refactor, the bubble's `mode` was tracked TWICE: in
 * `useBubbleStateMachine` (React state, the source of truth driving the
 * rendered pill) and in a local closure inside `useAudioLevels` (gating
 * the rAF visualizer loop + the dynamic `onLevel` IPC subscription).
 * The two trackers could drift because they were independent
 * implementations of the same transition table — a change to one (e.g.
 * adding `blocked` / `cancelling` / `permission_revoked` /
 * `paste_failed`) silently left the other stale.
 *
 * This reducer is the single transition table. It is consumed by:
 *   - `useBubbleBridge` — to keep the bridge's authoritative mode ref
 *     in lockstep with the event stream, updated BEFORE fan-out so
 *     every consumer handler reads the current mode synchronously.
 *   - `useBubbleStateMachine` — for its React `mode` state (via
 *     functional `setMode`), preserving the queued-update semantics of
 *     the original inline logic (e.g. hide → setState in the same
 *     batch).
 *
 * The transition table below reproduces the pre-refactor
 * `useBubbleStateMachine` logic verbatim:
 *   - `show` → `recording`, unless already `transcribing` (the backend
 *     may call `set_state("transcribing")` before `show()` re-fires).
 *   - `hide` → `fading` when transcribing (two-stage fade-out), else
 *     unchanged (the exit animation is driven by `exitTick`).
 *   - `setState` → the 8-state mapping, with a `fading` guard (an
 *     in-progress fade-out is only interrupted by a new `recording`).
 */
export function nextBubbleMode(
	prev: BubbleMode,
	event:
		| { type: "show" }
		| { type: "hide" }
		| { type: "setState"; state: string },
): BubbleMode {
	switch (event.type) {
		case "show":
			return prev === "transcribing" ? prev : "recording";
		case "hide":
			return prev === "transcribing" ? "fading" : prev;
		case "setState": {
			const s = event.state;
			// Recording interrupts the fading → exit transition (e.g. the
			// user starts a new dictation while the previous transcribing
			// pill is still fading out).
			if (s === "recording" && prev === "fading") return "recording";
			// Ignore non-recording state changes while fading out (exit
			// in progress).
			if (prev === "fading") return prev;
			if (s === "transcribing") return "transcribing";
			if (s === "idle") return "idle";
			if (s === "recording") return "recording";
			if (s === "error") return "error";
			if (s === "blocked") return "blocked";
			if (s === "cancelling") return "cancelling";
			if (s === "permission_revoked") return "permission_revoked";
			if (s === "paste_failed") return "paste_failed";
			return prev;
		}
	}
}

/**
 * Normalise the `bubble:set-state` payload into a state string + an
 * optional message + an optional partial transcript. The IPC type is
 * `(state: string) => void`, but the runtime payload MAY be a richer
 * object once the backend + main process are extended to forward error
 * reasons (`message`) or live partial transcription text (`transcript`,
 * XA-6-2). Defensive duck-typing keeps consumers forward-compatible
 * without requiring a type-system change to
 * `BubbleWindowExtras.onSetState`.
 *
 * Shared by `useBubbleStateMachine` (React state + side effects) and
 * `useBubbleBridge` (the authoritative mode ref) so both normalize the
 * payload identically.
 */
export function parseSetStatePayload(arg: unknown): {
	state: string;
	message: string | null;
	transcript: string | null;
	/**
	 * Tri-state engine capability from the backend's one-time
	 * live-preview signal: `false` when the active engine cannot
	 * stream partials (no `transcribe_words`), `true` when explicitly
	 * supported, `null` when the payload doesn't say (legacy/other
	 * publishers). Consumers keep the last explicit value while the
	 * recording continues.
	 */
	livePreviewSupported: boolean | null;
} {
	if (typeof arg === "string") {
		return {
			state: arg,
			message: null,
			transcript: null,
			livePreviewSupported: null,
		};
	}
	if (arg && typeof arg === "object" && "state" in arg) {
		const obj = arg as {
			state: unknown;
			message?: unknown;
			transcript?: unknown;
			live_preview_supported?: unknown;
		};
		const stateStr =
			typeof obj.state === "string" ? obj.state : String(obj.state);
		const message =
			typeof obj.message === "string" && obj.message.length > 0
				? obj.message
				: null;
		const transcript =
			typeof obj.transcript === "string" && obj.transcript.length > 0
				? obj.transcript
				: null;
		const livePreviewSupported =
			typeof obj.live_preview_supported === "boolean"
				? obj.live_preview_supported
				: null;
		return {
			state: stateStr,
			message,
			transcript,
			livePreviewSupported,
		};
	}
	return {
		state: String(arg),
		message: null,
		transcript: null,
		livePreviewSupported: null,
	};
}
