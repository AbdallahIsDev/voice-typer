/**
 * useMediaQuery — subscribes to a CSS media query and re-renders on change.
 *
 * BG-64 (partial): introduced so App.tsx can auto-collapse the sidebar when
 * the window narrows below the `640px` breakpoint. The hook is generic
 * (not hardcoded to the sidebar breakpoint) so other callers can subscribe
 * to arbitrary queries (e.g. `prefers-reduced-motion`, `min-width: 1024px`).
 *
 * SSR / non-browser guard: returns `false` when `window` is undefined so the
 * hook is safe to call from module-scope code that may execute during SSR
 * or in a Node-based test runner that doesn't define `window.matchMedia`.
 *
 * @param query  A CSS media query string, e.g. `"(max-width: 640px)"`.
 * @returns      `true` when the query currently matches, `false` otherwise.
 */
import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
	const [matches, setMatches] = useState<boolean>(() => {
		if (typeof window === "undefined" || !window.matchMedia) return false;
		return window.matchMedia(query).matches;
	});

	useEffect(() => {
		if (typeof window === "undefined" || !window.matchMedia) return;
		const mql = window.matchMedia(query);
		// Sync with the current match state on mount and whenever the
		// query string changes — `useState`'s lazy initializer only
		// runs once, so a query change requires an explicit re-sync.
		setMatches(mql.matches);
		const handler = (e: MediaQueryListEvent) => setMatches(e.matches);
		mql.addEventListener("change", handler);
		return () => mql.removeEventListener("change", handler);
	}, [query]);

	return matches;
}
