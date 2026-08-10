//data-fetch + refresh + event-subscription hook extracted
// from `pages/Dashboard.tsx` (lines ~243-402 of the pre-split file).
//
// Owns the dashboard's `data` / `configRaw` / `refreshing` React state,
// the `refreshData` fetch, the manual-refresh wrapper, and the two
// `usePythonEvent` subscriptions (`transcription_final` and
// `history_changed`) that debounced-refresh the dashboard after backend
// state changes. Also owns the cleanup effect that clears the pending
// debounced-refresh timer on unmount.
//
// Behaviour is identical to the pre-split inline implementation. The
// only structural change is that the dead `loadData` wrapper
// (`const loadData = useCallback(async () => { await refreshData(); }, ...)`,
//Finding 10) is removed — `refreshData` is called directly at
// both former `loadData` call sites (initial mount + manual refresh).

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePythonEvent } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, TodayStats } from "@/types/ipc";
import type { DashboardData } from "../lib/streaks";
import { computeDailyActivity, computeStreaks } from "../lib/streaks";

/**
 * Arguments for {@link useDashboardData}.
 *
 * `call` is the Python IPC call function from `usePython()` — passed in
 * (rather than re-fetched) so the parent component owns the bridge
 * lifecycle and so the hook can be unit-tested with a stub `call`.
 */
export interface UseDashboardDataArgs {
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>;
}

export interface UseDashboardDataResult {
	data: DashboardData | null;
	configRaw: VoiceTyperConfig | null;
	/** Backend config directory (from get_status) for the data-path display. */
	configDir: string;
	refreshing: boolean;
	refreshData: () => Promise<void>;
	handleManualRefresh: () => Promise<void>;
	/**
	 * The debounced-refresh callback attached to both
	 * `transcription_final` and `history_changed` events. Exposed
	 * primarily for testability — production callers should not invoke
	 * it directly (the subscriptions inside the hook already do so).
	 */
	debouncedRefreshFromEvent: () => (() => void) | undefined;
	/** "Last updated" relative label (e.g. "5s ago") for the indicator. */
	agoLabel: string;
	/** Error from the most recent `refreshData()` call, or `null` if the last call succeeded or hasn't been called yet. */
	fetchError: string | null;
}

export function useDashboardData({
	call,
}: UseDashboardDataArgs): UseDashboardDataResult {
	// Per-instance cache ref (replaced the prior module-level
	// `let _cachedData` mutable binding). The initial `useState` value
	// seeds from this ref so the first render after a navigation back
	// to the dashboard shows the previously-fetched data instead of
	// flashing empty.
	const cachedDataRef = useRef<DashboardData | null>(null);
	const [data, setData] = useState<DashboardData | null>(cachedDataRef.current);
	// R7-F18: removed dead `const [, setLoading] = useState(true)`.
	const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null);
	const [configDir, setConfigDir] = useState<string>("");
	// F4 (b-review Finding 11): "Last updated" indicator state. The
	// per-instance `cachedDataRef` survives re-renders within the same
	// mount, so we mark the timestamp after each successful refreshData()
	// to surface staleness to the user.
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const [fetchError, setFetchError] = useState<string | null>(null);

	/** Fetch all dashboard data from the Python backend. */
	const refreshData = useCallback(async () => {
		try {
			const [cfg, todayStats, history, totalCount, status] = await Promise.all([
				call<VoiceTyperConfig>("get_config"),
				call<TodayStats>("get_today_stats").catch(() => ({
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				})),
				call<HistoryRecord[]>("get_history", { limit: 200 }).catch(
					() => [] as HistoryRecord[],
				), //capped at 200
				// Fetch the TRUE total dictation count via the dedicated
				// `get_history_count` IPC. The `get_history({limit: 200})`
				// sample above is still used for daily-activity / streak
				// computation (where 200 rows is a sufficient sample), but
				// the "Total Dictations" stat card now reflects the actual
				// row count instead of capping at 200 forever.
				call<{ count: number }>("get_history_count").catch(() => ({
					count: 0,
				})),
				// Fetch the backend config directory (for the data-path display).
				// Returns null on failure — the caller falls back to the default path.
				call<{ config_dir?: string } | null>("get_status").catch(() => null),
			]);

			const recs = history ?? [];
			const dailyActivity = computeDailyActivity(recs);
			const streaks = computeStreaks(recs);
			const favoritesCount = recs.filter((r) => r.favorite > 0).length;

			// Total all-time stats
			let totalChars = 0,
				totalDuration = 0;
			for (const r of recs) {
				totalChars += r.char_count ?? 0;
				totalDuration += r.duration ?? 0;
			}

			const newData: DashboardData = {
				todayCount: todayStats?.count ?? 0,
				todayChars: todayStats?.chars ?? 0,
				todayWordCount: todayStats?.word_count ?? 0,
				todayDuration: todayStats?.duration ?? 0,
				// Prefer the dedicated count endpoint; fall back to the
				// sampled-history length only when the endpoint is
				// unavailable (e.g. older backend that doesn't expose
				// `get_history_count` yet). `totalCount?.count` is 0 on
				// both empty-DB and IPC-failure — the empty-DB case is
				// correct, and the IPC-failure case surfaces a 0 stat
				// (better than a stale 200).
				totalCount: totalCount?.count ?? recs.length,
				totalChars,
				totalDuration,
				favoritesCount,
				model: cfg?.model_size ?? t("analytics.unknown"),
				device: cfg?.device ?? t("analytics.unknown"),
				language: cfg?.language || t("analytics.auto"),
				dailyActivity,
				currentStreak: streaks.current,
				maxStreak: streaks.max,
				activeDays: streaks.activeDays,
			};
			cachedDataRef.current = newData;
			setData(newData);
			setConfigRaw(cfg ?? null);
			if (status?.config_dir) setConfigDir(status.config_dir);
			setFetchError(null);
		} catch (err) {
			// Surface refresh failures to the user instead of
			// silently swallowing them.  The previous implementation
			// caught and ignored ALL errors, so a backend disconnect
			// during a background refresh (e.g. transcription_final
			// trigger) left the user staring at stale data with no
			// indication that the refresh failed.  We now show a
			// toast.error so the user knows to retry manually via the
			// LastUpdatedIndicator refresh button.
			const message = t("analytics.refreshFailed");
			console.error("Dashboard refresh failed:", err);
			toast.error(message);
			setFetchError(message);
		} finally {
			// F4: bump the "last updated" timestamp after each refresh
			// attempt (success or failure) so the indicator stays accurate.
			markUpdated();
		}
	}, [call, markUpdated]);

	//Finding 10: dead `loadData` wrapper removed. The pre-split
	// file wrapped `refreshData` in a no-op `useCallback` named
	// `loadData` and called `loadData()` at the two sites below —
	// `handleManualRefresh` and the mount `useEffect`. Both now call
	// `refreshData` directly.

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	// Wraps `refreshData()` so we can flip a `refreshing` flag for the
	// button's spinner state without disturbing the page's main
	// loading state (which is unused in Dashboard — the page renders
	// a full-page skeleton via `if (!data) return <DashboardSkeleton />`).
	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await refreshData();
		} finally {
			setRefreshing(false);
		}
	}, [refreshData]);

	// ── Proactive background refresh after new transcriptions ────────
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

	// stale-data flag. Set to `true` when a `transcription_final`
	// or `history_changed` event arrives while the window is hidden
	// (document.visibilityState !== "visible"). The visibilitychange
	// listener below checks this flag on focus and triggers a single
	// debounced refresh — so background events don't fire 4 IPC calls
	// each (get_config + get_today_stats + get_history(200) +
	// get_history_count) while the user isn't looking at the page. The
	// next focus collapses the backlog into ONE fetch.
	const staleRef = useRef(false);

	// F11-FIX (b-review Finding 11): invalidate the cached dashboard data
	// when history changes through a path OUTSIDE this page (clear/delete/
	// restore/favorite from the tray menu, another window, or a CLI tool).
	// Mirrors the transcription_final refresh. Both subscriptions share
	// the same debounced-refresh callback.
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
		refreshTimer.current = setTimeout(refreshData, 500);
		return undefined;
	}, [refreshData]);

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
	usePythonEvent("history_changed", debouncedRefreshFromEvent);

	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
		};
	}, []);

	useEffect(() => {
		refreshData();
	}, [refreshData]);

	return {
		data,
		configRaw,
		configDir,
		refreshing,
		refreshData,
		handleManualRefresh,
		debouncedRefreshFromEvent,
		agoLabel,
		fetchError,
	};
}
