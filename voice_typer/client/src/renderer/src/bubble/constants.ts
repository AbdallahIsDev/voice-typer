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
	"no-drag ms-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground dark:hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-0";
