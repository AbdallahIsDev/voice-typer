import { useCallback, useEffect, useState } from "react";
import { t, tChoice } from "@/i18n/i18n";

export function useLastUpdated(): {
	lastUpdated: number | null;
	markUpdated: () => void;
	agoLabel: string;
	/** True while a `withRefresh`-wrapped op is in-flight. */
	refreshing: boolean;
	withRefresh: <T>(op: () => Promise<T>) => Promise<T>;
} {
	const [lastUpdated, setLastUpdated] = useState<number | null>(null);
	// `now` ticks every 5s so the relative "Xs ago" label refreshes
	// without coupling to the page's render cycle.
	const [now, setNow] = useState(() => Date.now());
	// Centralized refreshing flag with guaranteed cleanup.
	const [refreshing, setRefreshing] = useState(false);

	useEffect(() => {
		// gate the 5s `setNow` interval on
		// `document.visibilityState === "visible"`. The prior
		// implementation unconditionally ran the interval, which
		// re-rendered every mounted page that consumes `useLastUpdated`
		// (Home, History, Models, Microphone, Dashboard) even when the
		// tab was hidden, pure waste (no one is looking at the "Xs ago"
		// label when the tab is in the background). Browsers throttle
		// hidden-tab intervals to ~1 Hz but don't pause them, so the
		// interval still fires; we CLEAR it on hide and RE-ARM on show
		// via a `visibilitychange` listener so no ticks fire at all
		// while hidden. On re-show, the first tick re-syncs `now` (at
		// most 5s lag, which is acceptable for a relative-time label
		// that granularity-rounds to "Xs ago" / "Xm ago" / "Xh ago").
		let id: ReturnType<typeof setInterval> | null = null;
		const arm = () => {
			if (id !== null) return;
			id = setInterval(() => setNow(Date.now()), 5000);
		};
		const disarm = () => {
			if (id === null) return;
			clearInterval(id);
			id = null;
		};
		const handleVisibility = () => {
			if (typeof document === "undefined") return;
			if (document.visibilityState === "visible") {
				arm();
			} else {
				disarm();
			}
		};
		// Initial arm, only if the tab is visible at mount.
		if (
			typeof document === "undefined" ||
			document.visibilityState === "visible"
		) {
			arm();
		}
		if (typeof document !== "undefined") {
			document.addEventListener("visibilitychange", handleVisibility);
		}
		return () => {
			if (typeof document !== "undefined") {
				document.removeEventListener("visibilitychange", handleVisibility);
			}
			disarm();
		};
	}, []);

	const markUpdated = useCallback(() => {
		setLastUpdated(Date.now());
	}, []);

	// `withRefresh` wraps an async op with the `refreshing` flag.
	// React guarantees `setRefreshing` (the setter returned by
	// `useState`) is stable across renders, so listing it in the
	// deps array keeps `withRefresh` itself stable, no `useRef`
	// indirection is required. Earlier this used a `setRefreshingRef`
	// to hold the setter, which added a ref-mutation on every render
	// (a side effect during the render phase) without buying any
	// extra stability. The `try/finally` invariant, `refreshing`
	// is cleared on BOTH success and error, is preserved.
	const withRefresh = useCallback(
		async <T>(op: () => Promise<T>): Promise<T> => {
			setRefreshing(true);
			try {
				return await op();
			} finally {
				// GUARANTEED to run on both success and error —
				// this is the fix for "refreshing stuck on error".
				setRefreshing(false);
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
			agoLabel = tChoice("common.lastUpdatedSecondsAgo", seconds);
		} else {
			const minutes = Math.floor(seconds / 60);
			if (minutes < 60) {
				agoLabel = tChoice("common.lastUpdatedMinutesAgo", minutes);
			} else {
				const hours = Math.floor(minutes / 60);
				agoLabel = tChoice("common.lastUpdatedHoursAgo", hours);
			}
		}
	}

	return { lastUpdated, markUpdated, agoLabel, refreshing, withRefresh };
}
