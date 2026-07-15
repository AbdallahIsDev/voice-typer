import { useCallback, useEffect, useState } from "react";
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
 * @returns `lastUpdated` (epoch ms or null), `markUpdated` (call after
 *          a successful refresh to bump the timestamp), and `agoLabel`
 *          (i18n-localized relative label).
 */
export function useLastUpdated(): {
	lastUpdated: number | null;
	markUpdated: () => void;
	agoLabel: string;
} {
	const [lastUpdated, setLastUpdated] = useState<number | null>(null);
	// `now` ticks every 5s so the relative "Xs ago" label refreshes
	// without coupling to the page's render cycle.
	const [now, setNow] = useState(() => Date.now());

	useEffect(() => {
		const id = setInterval(() => setNow(Date.now()), 5000);
		return () => clearInterval(id);
	}, []);

	const markUpdated = useCallback(() => {
		setLastUpdated(Date.now());
	}, []);

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

	return { lastUpdated, markUpdated, agoLabel };
}
