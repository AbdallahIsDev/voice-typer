// Shared "scroll to a Settings row and ring it" machinery for the
// deep-link effects (consent + cross-page search).
// Both deep-link paths in `pages/settings/hooks/useSettingsDeepLinks.ts`
// implement the same shape: a one-shot guard, a bounded retry loop
// waiting for the target row to render, the same `scrollIntoView` call,
// the same 2600ms highlight-lifetime timer, and the same cleanup. They
// `pages/Settings.tsx` at the time of the review) that had already
// begun drifting in ring mechanism, this helper owns the shared
// machinery ONCE; each caller supplies only its row matcher and how
// the highlight ring is applied / cleared (consent: React state ring
// consumed by PrivacySettingsSection; search: imperative ring classes
// on the matched element).

/** Shared mutable state both deep-link paths coordinate through. */
export interface ScrollRowHighlightShared {
	scrolledTarget: { current: string | null };
	highlightTimer: { current: ReturnType<typeof setTimeout> | null };
}

export interface ScrollToRowWithHighlightOptions {
	/** Deep-link target identity used by the one-shot guard. */
	target: string;
	matchFn: () => HTMLElement | undefined;
	onFound?: (el: HTMLElement) => void;
	onExpire: (el: HTMLElement) => void;
	/** How long the highlight ring stays on (ms). */
	highlightLifetimeMs?: number;
	/** Shared one-shot guard + ring timer (see {@link ScrollRowHighlightShared}). */
	shared: ScrollRowHighlightShared;
}

/** Bounded-retry budget: attempts × delay ≈ 3s (stale targets can't spin forever). */
const MAX_ATTEMPTS = 60;
const RETRY_DELAY_MS = 50;
/** Default ring lifetime, matches the historical behavior of both twins. */
const DEFAULT_HIGHLIGHT_LIFETIME_MS = 2600;

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
		// Bounded retry (~3s), a stale target can't spin forever.
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
