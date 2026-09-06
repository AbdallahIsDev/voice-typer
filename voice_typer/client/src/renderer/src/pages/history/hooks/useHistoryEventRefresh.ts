// History background-event refresh + manual refresh hook.
//
// Extracted from `pages/History.tsx` (page-root slimming): the whole
// background-refresh pipeline — the 500ms-debounced
// `transcription_final` / `history_changed` handler, the
// hidden-window stale flag, the visibilitychange one-shot refresh, the
// `refreshing` spinner state, and the manual refresh wrapper — is one
// cohesive concern that belongs in a named, testable hook instead of
// the page root. The data fetch itself stays in `useHistoryCache`
// (`refreshFromEvent` / passed-in `runLoad`); this hook only owns WHEN
// those run.
//
// The page must pass its `runLoad` wrapper (the fresh-load path that
// also resets the visible-row window) so the manual refresh button
// behaves exactly like the other fresh loads (mount, search, retry).

import { useCallback, useEffect, useRef, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";

export interface UseHistoryEventRefreshOptions {
	/** The cache hook's background re-fetch (does NOT flip `loading`). */
	refreshFromEvent: () => Promise<void>;
	/** The page's fresh-load wrapper (resets the visible-row window). */
	runLoad: (query?: string, favoritesOnly?: boolean) => Promise<void>;
}

export interface UseHistoryEventRefreshReturn {
	/** Refresh-button handler (LastUpdatedIndicator's onRefresh). */
	handleManualRefresh: () => Promise<void>;
	/** True while a manual refresh is in flight (drives the indicator). */
	refreshing: boolean;
}

/**
 * Background-event + manual refresh pipeline for the History page. See
 * the file header for the extraction rationale.
 */
export function useHistoryEventRefresh({
	refreshFromEvent,
	runLoad,
}: UseHistoryEventRefreshOptions): UseHistoryEventRefreshReturn {
	const [refreshing, setRefreshing] = useState(false);

	// stale-data flag. Set to `true` when a `transcription_final`
	// or `history_changed` event arrives while the window is hidden
	// (document.visibilityState !== "visible"). The visibilitychange
	// listener below checks this flag on focus and triggers a single
	// debounced refresh — so background events don't fire IPC calls
	// while the user isn't looking at the page. The next focus
	// collapses the backlog into ONE fetch (per-page; only the visible
	// page's listener actually runs because only the mounted page
	// subscribes).
	const staleRef = useRef(false);
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await runLoad();
		} finally {
			setRefreshing(false);
		}
	}, [runLoad]);

	// extracted `debouncedRefreshFromEvent` via useCallback.
	// Wraps `refreshFromEvent` (owned by useHistoryCache) in a 500ms
	// debounce so rapid transcription_final / history_changed events
	// coalesce into a single backend fetch.
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		// skip the IPC round-trips when the window is hidden.
		// The visibilitychange listener below will trigger a single
		// refresh when the user returns to the page.
		if (
			typeof document !== "undefined" &&
			document.visibilityState !== "visible"
		) {
			staleRef.current = true;
			return undefined;
		}
		if (refreshTimer.current) clearTimeout(refreshTimer.current);
		refreshTimer.current = setTimeout(async () => {
			try {
				await refreshFromEvent();
			} catch (e) {
				console.warn("[renderer:History] background refresh failed:", e);
			}
		}, 500);
		return undefined;
	}, [refreshFromEvent]);

	// refresh on focus when stale. When the window regains
	// visibility AND a stale flag was set by a background event, fire
	// a single debounced refresh.
	useEffect(() => {
		const onVisibility = () => {
			if (document.visibilityState === "visible" && staleRef.current) {
				staleRef.current = false;
				debouncedRefreshFromEvent();
			}
		};
		document.addEventListener("visibilitychange", onVisibility);
		return () => {
			document.removeEventListener("visibilitychange", onVisibility);
		};
	}, [debouncedRefreshFromEvent]);

	usePythonEvent("transcription_final", debouncedRefreshFromEvent);
	// invalidate cache on external history_changed events.
	usePythonEvent("history_changed", debouncedRefreshFromEvent);

	// Clean up the pending refresh timer on unmount (the search
	// debounce's timer is cleaned up by useHistorySearchReload).
	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
		};
	}, []);

	return { handleManualRefresh, refreshing };
}
