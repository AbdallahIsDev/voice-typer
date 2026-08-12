//localStorage cache helpers extracted from
// Home.tsx. Each helper takes the component-scoped `MutableRefObject`
// that owns the in-memory hit-avoidance cache, so the helpers remain
// pure (no module-level mutable state — the previous `let _cachedRecent`
// / `let _cachedStats` bindings leaked across HMR / test re-mounts and
// were not React-aware).
//
// localStorage is still the persistence layer; the ref is purely an
// in-memory hit-avoidance cache for the current component instance.

import type { MutableRefObject } from "react";
import type { HistoryRecord, TodayStats } from "@/types/ipc";
import { RECENT_CACHE_KEY, STATS_CACHE_KEY } from "./constants";

/**
 * Read the cached recent-activity list from the ref (if populated) or
 * fall back to localStorage. The ref is populated on first read so
 * subsequent calls skip the JSON.parse cost.
 */
export function loadCachedRecent(
	ref: MutableRefObject<HistoryRecord[]>,
): HistoryRecord[] {
	if (ref.current.length > 0) return ref.current;
	try {
		const raw = localStorage.getItem(RECENT_CACHE_KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (Array.isArray(parsed)) ref.current = parsed as HistoryRecord[];
		}
	} catch (e) {
		// localStorage unavailable or payload malformed — non-fatal.
		console.warn("[renderer:Home] loadCachedRecent failed:", e);
	}
	return ref.current;
}

/**
 * Read the cached today-stats from the ref (if populated) or fall back
 * to localStorage. The shape sanity-check (`parsed.count` is a number)
 * guards against partial / stale payloads from older renderer versions.
 */
export function loadCachedStats(
	ref: MutableRefObject<TodayStats | null>,
): TodayStats | null {
	if (ref.current !== null) return ref.current;
	try {
		const raw = localStorage.getItem(STATS_CACHE_KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (
				parsed &&
				typeof parsed === "object" &&
				typeof (parsed as { count?: unknown }).count === "number"
			) {
				ref.current = parsed as TodayStats;
			}
		}
	} catch (e) {
		// localStorage unavailable or payload malformed — non-fatal.
		console.warn("[renderer:Home] loadCachedStats failed:", e);
	}
	return ref.current;
}

/**
 * Write the recent-activity list to both the in-memory ref and
 * localStorage. Quota-exceeded / unavailable localStorage is non-fatal
 * (the ref still holds the value for the current mount).
 */
export function persistRecent(
	ref: MutableRefObject<HistoryRecord[]>,
	recent: HistoryRecord[],
): void {
	ref.current = recent;
	try {
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify(recent));
	} catch (e) {
		// Quota exceeded or unavailable — non-fatal.
		console.warn("[renderer:Home] persistRecent failed:", e);
	}
}

/**
 * Write the today-stats payload to both the in-memory ref and
 * localStorage. Quota-exceeded / unavailable localStorage is non-fatal
 * (the ref still holds the value for the current mount).
 */
export function persistStats(
	ref: MutableRefObject<TodayStats | null>,
	stats: TodayStats,
): void {
	ref.current = stats;
	try {
		localStorage.setItem(STATS_CACHE_KEY, JSON.stringify(stats));
	} catch (e) {
		// Quota exceeded or unavailable — non-fatal.
		console.warn("[renderer:Home] persistStats failed:", e);
	}
}
