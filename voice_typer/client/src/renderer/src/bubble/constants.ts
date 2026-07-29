/**
 * Bubble overlay package — shared constants.
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16). All visualisation tuning knobs and the shared button
 * `className` live here so the three button components (`BubbleMicButton`,
 * `BubbleStopButton`, `BubbleDismissButton`) stay in sync by construction
 * rather than by copy/paste.
 */

// ── Types ────────────────────────────────────────────────────────────

export type BubbleMode =
	| "recording"
	| "transcribing"
	| "idle"
	| "fading"
	| "error";

export type AnimState = "enter" | "exit" | "";

export type BubbleAction = "mic" | "dismiss";

// ── Visualizer constants ─────────────────────────────────────────────

export const DOT_COUNT = 7;
export const MIN_HEIGHT = 5;
// BUBBLE-FIX-5.1: reduced from 32 → 22 to fit inside the h-6 (24px)
// wrapper with 2px vertical headroom.
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
 * `no-drag` opt-out so clicks bubble through the Electron `-webkit-app-region: drag`
 * region) so they read as siblings of one pill.
 *
 * Extracted from the previous inline 250-char string (one copy per
 * button) into a single module-level constant — DR-16 DRY fix.
 *
 * `ms-1` (margin-inline-start) replaces the original `ml-1` for RTL
 * safety: in LTR it renders as margin-left; in RTL (ar locale) it
 * flips to margin-right automatically.
 */
export const BUBBLE_BUTTON_CLASS =
	"no-drag ms-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-white/10 dark:hover:text-white";
