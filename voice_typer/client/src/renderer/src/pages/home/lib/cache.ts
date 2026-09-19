// Home.tsx. Each helper takes the component-scoped `RefObject`
// that owns the in-memory hit-avoidance cache, so the helpers remain
// pure (no module-level mutable state, the previous `let _cachedRecent`
// / `let _cachedStats` bindings leaked across HMR / test re-mounts and
// were not React-aware).
// localStorage is still the persistence layer; the ref is purely an
// in-memory hit-avoidance cache for the current component instance.

import type { RefObject } from "react";
import type { HistoryRecord, TodayStats } from "@/types/ipc";
import { RECENT_CACHE_KEY, STATS_CACHE_KEY } from "./constants";

function isCacheableHistoryRecord(value: unknown): value is HistoryRecord {
	if (typeof value !== "object" || value === null) return false;
	const r = value as { id?: unknown; text?: unknown; timestamp?: unknown };
	return (
		typeof r.id === "number" &&
		typeof r.text === "string" &&
		typeof r.timestamp === "string"
	);
}

export function loadCachedRecent(
	ref: RefObject<HistoryRecord[]>,
): HistoryRecord[] {
	if (ref.current.length > 0) return ref.current;
	try {
		const raw = localStorage.getItem(RECENT_CACHE_KEY);
		if (raw) {
			const parsed = JSON.parse(raw);
			if (Array.isArray(parsed)) {
				ref.current = parsed.filter(isCacheableHistoryRecord);
			}
		}
	} catch (e) {
		// localStorage unavailable or payload malformed, non-fatal.
		console.warn("[renderer:Home] loadCachedRecent failed:", e);
	}
	return ref.current;
}

export function loadCachedStats(
	ref: RefObject<TodayStats | null>,
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
		// localStorage unavailable or payload malformed, non-fatal.
		console.warn("[renderer:Home] loadCachedStats failed:", e);
	}
	return ref.current;
}

export function persistRecent(
	ref: RefObject<HistoryRecord[]>,
	recent: HistoryRecord[],
): void {
	ref.current = recent;
	try {
		localStorage.setItem(RECENT_CACHE_KEY, JSON.stringify(recent));
	} catch (e) {
		// Quota exceeded or unavailable, non-fatal.
		console.warn("[renderer:Home] persistRecent failed:", e);
	}
}

export function persistStats(
	ref: RefObject<TodayStats | null>,
	stats: TodayStats,
): void {
	ref.current = stats;
	try {
		localStorage.setItem(STATS_CACHE_KEY, JSON.stringify(stats));
	} catch (e) {
		// Quota exceeded or unavailable, non-fatal.
		console.warn("[renderer:Home] persistStats failed:", e);
	}
}
