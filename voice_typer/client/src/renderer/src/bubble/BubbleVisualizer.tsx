/**
 * Bubble overlay package — `BubbleVisualizer` (recording mode).
 *
 * Extracted from the former `bubble-components.tsx` monolith (PVT-067 /
 * DR-16).
 *
 * The recording-mode pill content: a destructive-token "● REC"
 * indicator + 7-bar spectrum visualiser. The bars are animated by
 * `useBubbleLifecycle`'s rAF loop via the shared `dotRefs`.
 *
 * The 7 bar <span>s live inside a `<div class="gap-[3px]">` wrapper
 * — this preserves the `Bubble.test.tsx` selector
 * `.gap-[3px] > span` which expects exactly 7 bars.
 *
 * The per-dot ref setters are memoised once per `dotRefs`
 * instance via `useMemo` so React doesn't call ref-cleanup + re-attach
 * on every render (the previous inline arrow was a fresh closure every
 * render, which React treats as a ref change). The index list is the
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
	// build the 7 stable ref setters once per `dotRefs` instance.
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
		<div className="flex h-6 items-center gap-1.5">
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
			<div className="flex h-6 items-center gap-0.75 ms-1" aria-hidden>
				{DOT_INDICES.map((i) => (
					<span
						key={i}
						ref={refSetters[i]}
						className="inline-block w-0.75 rounded-full bg-zinc-900 dark:bg-white"
						style={{ height: MIN_HEIGHT, opacity: 0.3 }}
					/>
				))}
			</div>
		</div>
	);
}
