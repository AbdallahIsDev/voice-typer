// History global-search debounced reload hook.
//
// Extracted from `pages/History.tsx` (page-root slimming): the debounced
// reload driven by the GLOBAL search store — the 200ms-delayed fresh
// load whenever the query (or the favorites filter) changes, plus the
// first-render guard that keeps the mount load from double-firing — is
// one cohesive concern. The page passes its `runLoad` fresh-load
// wrapper so a reload also resets the visible-row window exactly like
// every other fresh load.
//
// The pending search timer is cleaned up on unmount here (previously a
// single page-level effect cleared both this timer and the background
// refresh timer; each timer now lives with the hook that schedules it).

import { useEffect, useRef } from "react";

export interface UseHistorySearchReloadOptions {
	/** The active global-search query (from the useGlobalSearch store). */
	searchQuery: string;
	/** Whether the favorites-only filter is active. */
	favoritesOnly: boolean;
	/** The page's fresh-load wrapper (resets the visible-row window). */
	runLoad: (query?: string, favoritesOnly?: boolean) => Promise<void>;
}

/**
 * Schedules a debounced fresh reload whenever the global search query
 * or the favorites filter changes. See the file header for the
 * extraction rationale.
 */
export function useHistorySearchReload({
	searchQuery,
	favoritesOnly,
	runLoad,
}: UseHistorySearchReloadOptions): void {
	const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	// Guards the debounced-load effect so the initial mount load
	// (handled by the separate mount effect) is not re-fired when the
	// global query starts at "" — only query CHANGES trigger a reload.
	const isFirstRenderRef = useRef(true);

	// Debounced reload driven by the GLOBAL search store. The query now
	// lives in the title bar's global search bar; when it changes this
	// effect schedules a 200ms-delayed runLoad with the new query. The
	// first-render guard keeps the mount load from double-firing.
	useEffect(() => {
		if (isFirstRenderRef.current) {
			isFirstRenderRef.current = false;
			return;
		}
		if (searchTimer.current) clearTimeout(searchTimer.current);
		searchTimer.current = setTimeout(() => {
			runLoad(searchQuery, favoritesOnly);
		}, 200);
		return () => {
			if (searchTimer.current) {
				clearTimeout(searchTimer.current);
				searchTimer.current = null;
			}
		};
	}, [searchQuery, runLoad, favoritesOnly]);

	// Clean up a pending search timer on unmount so load() never fires
	// on an unmounted component.
	useEffect(() => {
		return () => {
			if (searchTimer.current) {
				clearTimeout(searchTimer.current);
				searchTimer.current = null;
			}
		};
	}, []);
}
