/**
 * Bubble overlay package — `BubbleVisualizer` (recording mode).
 *
 * The recording-mode pill content: a destructive-token "● REC"
 * indicator + 7-bar spectrum visualiser. The bars are animated by
 * `useBubbleLifecycle`'s rAF loop via the shared `dotRefs`.
 *
 * The 7 bar `<span>`s live inside a `<div class="gap-0.75">` wrapper
 * — this preserves the `Bubble.test.tsx` selector
 * `.gap-0\.75 > span` which expects exactly 7 bars.
 *
 * The per-dot ref setters are memoised once per `dotRefs` instance via
 * `useMemo` so React doesn't call ref-cleanup + re-attach on every
 * render (the previous inline arrow was a fresh closure every render,
 * which React treats as a ref change). The index list is the
 * module-level `DOT_INDICES` constant (no per-render `Array.from`).
 */
import { type RefObject, useMemo } from "react";
import { DOT_COUNT, DOT_INDICES, MIN_HEIGHT } from "./constants";
import { tf } from "./helpers";

export function BubbleVisualizer({
	dotRefs,
}: {
	dotRefs: RefObject<(HTMLSpanElement | null)[]>;
}) {
	// Build the 7 stable ref setters once per `dotRefs` instance.
	// The array identity is stable across renders (only changes if
	// `dotRefs` changes, which it doesn't in practice), so React's
	// reconciler sees the same ref callback on every render and skips
	// the detach/attach cycle.
	const refSetters = useMemo(
		() =>
			Array.from(
				{ length: DOT_COUNT },
				(_, i) => (el: HTMLSpanElement | null) => {
					dotRefs.current[i] = el;
				},
			),
		[dotRefs],
	);
	return (
		<div className="flex h-6 items-center gap-2">
			{/* REC indicator — destructive token, not hardcoded red. */}
			<span
				className="w-1.5 h-1.5 rounded-full bg-destructive animate-pulse"
				aria-hidden
			/>
			<span className="text-[10px] font-medium text-destructive">
				{tf("bubble.recordingLabel", "REC")}
			</span>
			{/* `ms-1` is the RTL-safe logical replacement for the old
			    physical `ml-1`. In LTR it renders as margin-left; in RTL
			    (ar locale) it flips to margin-right automatically. */}
			{/*
			 * Tailwind v4 dependency: `gap-0.75` and `w-0.75` (below)
			 * rely on Tailwind v4's dynamic spacing scale, where the
			 * numeric suffix is multiplied by `--spacing` (default
			 * `0.25rem`) to produce the final value. So `gap-0.75` =
			 * `gap: calc(0.75 * 0.25rem)` = `gap: 0.1875rem` ≈ 3px,
			 * and `w-0.75` = `width: 0.1875rem` ≈ 3px. Tailwind v3
			 * would reject these classes (only integer / known-named
			 * spacing values were valid); upgrading to v3 or earlier
			 * would require falling back to arbitrary values
			 * (`gap-[3px]` / `w-[3px]`). The project pins
			 * `tailwindcss@^4.3.2` in `client/package.json` — see the
			 * `@import "tailwindcss";` directive in `index.css`.
			 */}
			<div className="flex h-6 items-center gap-0.75 ms-1" aria-hidden>
				{DOT_INDICES.map((i) => (
					<span
						key={i}
						ref={refSetters[i]}
						className="inline-block w-0.75 rounded-full bg-(--text-primary)"
						style={{ height: MIN_HEIGHT, opacity: 0.3 }}
					/>
				))}
			</div>
		</div>
	);
}
