import { useCallback, useEffect, useRef, useState } from "react";
import { t } from "@/i18n/i18n";

/**
 * useLastUpdated — tracks when a page's data was last fetched and
 * exposes a relative "Xs ago" / "Xm ago" label that updates over time.
 *
 * F4 (b-review Finding 11): several pages (Home, History, Models,
 * Microphone, Dashboard) keep a module-level mutable cache that
 * survives React's mount/unmount lifecycle. The cache is only refreshed
 * by explicit user action, the `transcription_final` push event, or the
 * `config_changed` event — so if the backend state changes through any
 * other path while the renderer is open, the next navigation shows
 * stale data.
 *
 * The directive's "least-invasive" fix is to accept the staleness but
 * add a visible "Last updated Xs ago" indicator + a small refresh
 * button. This hook provides the timestamp tracking + relative label;
 * each page renders its own indicator (the refresh action differs per
 * page — `load()`, `loadConfig()`, `loadData()`, etc.).
 *
 * The `now` state ticks every 5 seconds so the relative label updates
 * without forcing the page to re-render on every second. 5s matches
 * the "Xs" granularity users expect for a "last updated" indicator
 * (sub-5s would be flickery; sub-minute would hide drift until it's
 * too late to notice).
 *
 * ── Fix #25-2: centralized `refreshing` state ──────────────────────────
 *
 * Previously every page that rendered a `<LastUpdatedIndicator />`
 * duplicated the same pattern at the call site:
 *
 *   const [refreshing, setRefreshing] = useState(false);
 *   const handleManualRefresh = useCallback(async () => {
 *       setRefreshing(true);
 *       try {
 *           await loadData();
 *       } finally {
 *           setRefreshing(false);  // ← runs on both success and error
 *       }
 *   }, [loadData]);
 *
 * That pattern is correct (try/finally clears `refreshing` on error)
 * but it has to be re-implemented per page — easy to forget the
 * `finally` block, in which case a single failed refresh leaves the
 * spinner stuck forever (the bug this fix guards against). This hook
 * now exposes a centralized `refreshing` flag plus a `withRefresh`
 * wrapper that owns the try/finally invariant. Pages can either:
 *
 *   (a) keep their existing local `refreshing` state (backward compat —
 *       nothing breaks), or
 *   (b) migrate to the hook's `refreshing` + `withRefresh` so the
 *       invariant lives in one place.
 *
 * `withRefresh` does NOT call `markUpdated` itself — the caller decides
 * whether to bump the timestamp (e.g. only on successful load, or
 * always after the attempt regardless of outcome). This preserves the
 * flexibility the existing pages need (Dashboard bumps in finally;
 * Home bumps in finally; Microphone/Settings bump in try).
 *
 * @returns `lastUpdated` (epoch ms or null), `markUpdated` (call after
 *          a successful refresh to bump the timestamp), `agoLabel`
 *          (i18n-localized relative label), `refreshing` (true while a
 *          `withRefresh`-wrapped op is in-flight), and `withRefresh`
 *          (wrap an async op to drive the `refreshing` flag with
 *          guaranteed cleanup on both success and error).
 */
export function useLastUpdated(): {
	lastUpdated: number | null;
	markUpdated: () => void;
	agoLabel: string;
	/** True while a `withRefresh`-wrapped op is in-flight. */
	refreshing: boolean;
	/**
	 * Wrap an async refresh operation with the `refreshing` flag.
	 *
	 * Sets `refreshing=true` before the op starts and `refreshing=false`
	 * in a `finally` block — so the flag is GUARANTEED to be cleared
	 * on both success AND error. This is the fix for "refreshing
	 * state stuck on after a failed refresh" — pages that use this
	 * wrapper can't forget the cleanup.
	 *
	 * Does NOT call `markUpdated` — the caller decides when to bump
	 * the timestamp (e.g. only on successful load, or always).
	 *
	 * @returns The wrapped op's resolved value (or rethrows its error).
	 */
	withRefresh: <T>(op: () => Promise<T>) => Promise<T>;
} {
	const [lastUpdated, setLastUpdated] = useState<number | null>(null);
	// `now` ticks every 5s so the relative "Xs ago" label refreshes
	// without coupling to the page's render cycle.
	const [now, setNow] = useState(() => Date.now());
	// Fix #25-2: centralized refreshing flag with guaranteed cleanup.
	const [refreshing, setRefreshing] = useState(false);

	useEffect(() => {
		const id = setInterval(() => setNow(Date.now()), 5000);
		return () => clearInterval(id);
	}, []);

	const markUpdated = useCallback(() => {
		setLastUpdated(Date.now());
	}, []);

	// Hold the latest `setRefreshing` in a ref so `withRefresh` can be
	// a stable callback (no dependency churn) without going stale.
	const setRefreshingRef = useRef(setRefreshing);
	setRefreshingRef.current = setRefreshing;

	const withRefresh = useCallback(
		async <T>(op: () => Promise<T>): Promise<T> => {
			setRefreshingRef.current(true);
			try {
				return await op();
			} finally {
				// GUARANTEED to run on both success and error —
				// this is the fix for "refreshing stuck on error".
				setRefreshingRef.current(false);
			}
		},
		[],
	);

	// Compute the relative label using the same i18n keys used by the
	// About page's formatRelativeTime helper (lessThanMinute, minutesAgo,
	// hoursAgo) so the vocabulary stays consistent across the app.
	let agoLabel: string;
	if (lastUpdated === null) {
		agoLabel = t("common.lastUpdatedNever");
	} else {
		const seconds = Math.max(0, Math.floor((now - lastUpdated) / 1000));
		if (seconds < 5) {
			agoLabel = t("common.lastUpdatedJustNow");
		} else if (seconds < 60) {
			agoLabel = t("common.lastUpdatedSecondsAgo", {
				count: String(seconds),
			});
		} else {
			const minutes = Math.floor(seconds / 60);
			if (minutes < 60) {
				agoLabel = t("common.lastUpdatedMinutesAgo", {
					count: String(minutes),
				});
			} else {
				const hours = Math.floor(minutes / 60);
				agoLabel = t("common.lastUpdatedHoursAgo", {
					count: String(hours),
				});
			}
		}
	}

	return { lastUpdated, markUpdated, agoLabel, refreshing, withRefresh };
}
