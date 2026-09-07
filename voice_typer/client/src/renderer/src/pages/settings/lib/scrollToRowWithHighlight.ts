// Shared "scroll to a Settings row and ring it" machinery for the
// deep-link effects (consent + cross-page search).
//
// Both deep-link paths in `pages/settings/hooks/useSettingsDeepLinks.ts`
// implement the same shape: a one-shot guard, a bounded retry loop
// waiting for the target row to render, the same `scrollIntoView` call,
// the same 2600ms highlight-lifetime timer, and the same cleanup. They
// previously existed as two ~85%-identical inline effects (in
// `pages/Settings.tsx` at the time of the review) that had already
// begun drifting in ring mechanism — this helper owns the shared
// machinery ONCE; each caller supplies only its row matcher and how
// the highlight ring is applied / cleared (consent: React state ring
// consumed by PrivacySettingsSection; search: imperative ring classes
// on the matched element).

/** Shared mutable state both deep-link paths coordinate through. */
export interface ScrollRowHighlightShared {
	/**
	 * Identity of the deep-link target that was last scrolled to
	 * (one-shot guard: re-renders of the same target — e.g. a config
	 * identity change — must not re-trigger a smooth re-center).
	 * Reset when the highlight lifetime elapses.
	 */
	scrolledTarget: { current: string | null };
	/**
	 * Timer handle for the highlight ring's lifetime. SHARED by both
	 * deep-link paths (only one target can be active at a time);
	 * re-arming a new target clears a previous timer.
	 */
	highlightTimer: { current: ReturnType<typeof setTimeout> | null };
}

export interface ScrollToRowWithHighlightOptions {
	/** Deep-link target identity used by the one-shot guard. */
	target: string;
	/**
	 * Finds the rendered row element. Called on each bounded-retry
	 * attempt until it returns an element (or the retry budget is
	 * exhausted).
	 */
	matchFn: () => HTMLElement | undefined;
	/**
	 * Called once when the row is found, right after the scroll —
	 * applies the highlight ring (e.g. adding ring classes). The
	 * highlight lifetime timer starts at this moment, so a slow
	 * config/page fetch can't burn the ring before the row renders.
	 */
	onFound?: (el: HTMLElement) => void;
	/**
	 * Called when the highlight lifetime elapses — clears the ring
	 * and (via the caller's state reset) disarms the deep-link.
	 */
	onExpire: (el: HTMLElement) => void;
	/** How long the highlight ring stays on (ms). */
	highlightLifetimeMs?: number;
	/** Shared one-shot guard + ring timer (see {@link ScrollRowHighlightShared}). */
	shared: ScrollRowHighlightShared;
}

/** Bounded-retry budget: attempts × delay ≈ 3s (stale targets can't spin forever). */
const MAX_ATTEMPTS = 60;
const RETRY_DELAY_MS = 50;
/** Default ring lifetime — matches the historical behavior of both twins. */
const DEFAULT_HIGHLIGHT_LIFETIME_MS = 2600;

/**
 * Scroll the deep-linked target row into view once it renders and ring
 * it for the highlight lifetime. Returns an effect cleanup that cancels
 * the pending first attempt (in-flight retries are guarded by a
 * `cancelled` flag).
 *
 * Byte-identical behavior contract (per deep-link path):
 *   - one-shot per target (`shared.scrolledTarget`),
 *   - first attempt on a 0ms timer, bounded retries 50ms apart (max 60),
 *   - `scrollIntoView({ behavior: "smooth", block: "center" })` when found,
 *   - ring lifetime starts when the row is actually found,
 *   - a previously armed highlight timer is cleared before re-arming.
 */
export function scrollToRowWithHighlight({
	target,
	matchFn,
	onFound,
	onExpire,
	highlightLifetimeMs = DEFAULT_HIGHLIGHT_LIFETIME_MS,
	shared,
}: ScrollToRowWithHighlightOptions): () => void {
	// One-shot: this target was already scrolled to (and its ring is
	// either active or already expired).
	if (shared.scrolledTarget.current === target) {
		return () => {};
	}
	let attempts = 0;
	let cancelled = false;
	const tryScroll = () => {
		if (cancelled) return;
		const el = matchFn();
		if (el) {
			shared.scrolledTarget.current = target;
			el.scrollIntoView?.({ behavior: "smooth", block: "center" });
			// Ring application is caller-specific (state-driven for
			// consent, class-based for search).
			onFound?.(el);
			// Ring lifetime starts now (row actually visible).
			if (shared.highlightTimer.current) {
				clearTimeout(shared.highlightTimer.current);
			}
			shared.highlightTimer.current = setTimeout(() => {
				onExpire(el);
				shared.scrolledTarget.current = null;
			}, highlightLifetimeMs);
			return;
		}
		// Bounded retry (~3s) — a stale target can't spin forever.
		if (attempts < MAX_ATTEMPTS) {
			attempts += 1;
			setTimeout(tryScroll, RETRY_DELAY_MS);
		}
	};
	const timer = setTimeout(tryScroll, 0);
	return () => {
		cancelled = true;
		clearTimeout(timer);
	};
}
