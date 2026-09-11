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
// SINGLE-SOURCE-OF-TRUTH change (data-consistency fix):
//   Previously the page pulled "today" stats from the backend's
//   `get_today_stats` aggregator while the chart/streaks/totals were
//   derived from a `get_history({limit: 200})` sample, two independent
//   computations that could disagree (and did: "Dictations Today: 0"
//   while "Active Days: 7" showed real activity, because the renderer
//   parsed the DB's UTC timestamps as local time, shifting evening /
//   early-morning dictations across calendar-day boundaries).
//   Now EVERY derived stat (today cards, chart bars, streaks, totals,
//   trends) is computed in one pass from ONE history sample (raised to
//   the backend's 500-row max), using UTC-correct day bucketing
//   (`parseUtcTimestamp` / `dateKey` in lib/streaks). The only
//   independent number is `totalCount`, from the dedicated
//   `get_history_count` IPC (the true all-time row count).
//
// Behaviour is otherwise identical to the pre-split inline
// implementation. The dead `loadData` wrapper (Finding 10) stays
// removed, `refreshData` is called directly at both former `loadData`
// call sites (initial mount + manual refresh).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePythonEvent } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import { resolveActiveModel } from "@/lib/utils/models";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, ModelStatusMap } from "@/types/ipc";
import {
	type ActivityChartData,
	buildActivityBars,
	type CorrectionStats,
	type CorrectionUsageSnapshot,
	computeCorrectionStats,
	computePeriodStats,
	computeStreaks,
	type DashboardData,
	dateKey,
	type PeriodStats,
	type RangeId,
} from "../lib/streaks";

/** History sample size for the dashboard's derived stats. */
export const DASHBOARD_SAMPLE_LIMIT = 500;

/**
 * Hot-path delta size for event-triggered refreshes (BP-159).
 *
 * A completed dictation appends exactly one row, so the event path
 * fetches only the 10-row head (+ count + corrections) instead of the
 * full 500-row sample. Any deviation (count jump, empty/malformed
 * delta, unseen-id mismatch) falls back to the full refresh.
 */
export const DASHBOARD_DELTA_LIMIT = 10;

/**
 * Pure `DashboardData` builder shared by the full and delta refresh
 * paths (BP-159 hot/cold split).
 *
 * Derives every stat (today cards, totals, streaks, setup line) from
 * ONE history sample with UTC-correct day bucketing, so both paths
 * compute byte-identical shapes from whatever sample they hand in.
 * Has no side effects, callers own cache writes + state updates.
 */
function buildDashboardData(args: {
	cfg: VoiceTyperConfig | null;
	recs: HistoryRecord[];
	totalCount: number;
	modelStatus: ModelStatusMap;
}): DashboardData {
	const { cfg, recs, totalCount, modelStatus } = args;
	const streaks = computeStreaks(recs);
	const favoritesCount = recs.filter((r) => r.favorite > 0).length;

	// Total all-time stats FROM THE SAMPLE, consistent with the
	// chart and streaks by construction. When the sample is
	// capped (totalCount > recs.length) the page shows a
	// "sampled from the last N dictations" footnote.
	let totalChars = 0,
		totalDuration = 0;
	for (const r of recs) {
		totalChars += r.char_count ?? 0;
		totalDuration += r.duration ?? 0;
	}

	// Today's bucket, computed from the SAME sample with the
	// same UTC-correct bucketing as the chart/streaks, so the
	// "Today" cards can never contradict the chart's today bar.
	// (Today's rows are always the newest, so they're always
	// inside the DESC-ordered sample.)
	const todayKey = dateKey(new Date().toISOString());
	let todayCount = 0,
		todayChars = 0,
		todayWordCount = 0,
		todayDuration = 0;
	for (const r of recs) {
		if (dateKey(r.timestamp) === todayKey) {
			todayCount++;
			todayChars += r.char_count ?? 0;
			todayWordCount += r.word_count ?? 0;
			todayDuration += r.duration ?? 0;
		}
	}

	// MODEL-STATE fix (source of the misleading "Model: tiny /
	// Device: CUDA" on fresh installs): the config values
	// (``model_size`` / ``device``) are NOT install state, the
	// app has no concrete default model, and device is only a
	// preference. Only report model/device when
	// the configured model's weights are actually on disk per
	// ``get_model_status``; otherwise surface ``null`` so the
	// display layer renders the localized "Not selected" state
	// (and the share image omits the setup line entirely). The
	// check itself lives in the SHARED ``resolveActiveModel``
	// (lib/utils/models.ts) so the About page's Diagnostics table
	// derives from the exact same truth.
	const modelStatusMap: ModelStatusMap = modelStatus ?? {};
	const { model: activeModel, device: activeDevice } = resolveActiveModel(
		cfg?.model_size ?? "",
		modelStatusMap,
		cfg?.device,
	);

	return {
		todayCount,
		todayChars,
		todayWordCount,
		todayDuration,
		// Prefer the dedicated count endpoint; fall back to the
		// sampled-history length only when the endpoint is
		// unavailable (e.g. older backend that doesn't expose
		// `get_history_count` yet). `totalCount?.count` is 0 on
		// both empty-DB and IPC-failure, the empty-DB case is
		// correct, and the IPC-failure case surfaces a 0 stat.
		totalCount,
		totalChars,
		totalDuration,
		favoritesCount,
		model: activeModel,
		device: activeDevice,
		language: cfg?.language ?? "",
		currentStreak: streaks.current,
		maxStreak: streaks.max,
		activeDays: streaks.activeDays,
		sampleSize: recs.length,
	};
}

// Module-cache key for the SWR seed (see lib/ipcCache.ts).
const DASHBOARD_CACHE_KEY = "analytics.dashboardData";

/**
 * Arguments for {@link useDashboardData}.
 *
 * `call` is the Python IPC call function from `usePython()`, passed in
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
	/** Selected analytics time range ("Today" / "7 Days" / …). */
	range: RangeId;
	setRange: (range: RangeId) => void;
	/** Range-aware stats (current window + previous window for trends). */
	period: PeriodStats;
	/** Range-aware chart bars (hourly for Today, daily otherwise). */
	activity: ActivityChartData;
	/** Range-aware corrections-applied totals from the vocabulary usage snapshot. */
	correctionStats: CorrectionStats;
	refreshData: () => Promise<void>;
	handleManualRefresh: () => Promise<void>;
	/**
	 * The debounced-refresh callback attached to both
	 * `transcription_final` and `history_changed` events. Exposed
	 * primarily for testability, production callers should not invoke
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
	// SWR seed: the initial `useState` value reads the MODULE-level IPC
	// cache (survives page unmount, so navigation back to the dashboard
	// shows the previously-fetched data instantly, the former
	// per-instance ref died with the unmounted page and never actually
	// survived navigation). `refreshData` still revalidates fresh data
	// over it every mount.

	// Ref mirror of `call` so `refreshData` keeps a STABLE identity
	// ([] deps). `call` is useCallback-stable in production, but test
	// mocks return a FRESH call per render, an identity churn would
	// re-fire the mount-load effect (refreshData → setData → re-render
	// → new call → loop → worker OOM). Same pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);

	const [data, setData] = useState<DashboardData | null>(
		() => peekIpcCache<DashboardData>(DASHBOARD_CACHE_KEY) ?? null,
	);
	// R7-F18: removed dead `const [, setLoading] = useState(true)`.
	const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null);
	const [configDir, setConfigDir] = useState<string>("");
	// F4 (b-review Finding 11): "Last updated" indicator state. We mark
	// the timestamp after each successful refreshData() to surface
	// staleness to the user.
	const { agoLabel, markUpdated } = useLastUpdated();
	// Ref mirror of `markUpdated`, same rationale as the callRef above.
	const markUpdatedRef = useRef(markUpdated);
	useEffect(() => {
		markUpdatedRef.current = markUpdated;
	}, [markUpdated]);
	const [refreshing, setRefreshing] = useState(false);
	const [fetchError, setFetchError] = useState<string | null>(null);

	// Selected time range, drives the stat cards + chart together.
	const [range, setRange] = useState<RangeId>("7d");

	// The history sample backing every derived stat (kept so period /
	// activity memos recompute when the data refreshes).
	const [sample, setSample] = useState<HistoryRecord[]>([]);
	// Per-correction usage snapshot from `get_correction_usage`.
	const [correctionUsage, setCorrectionUsage] =
		useState<CorrectionUsageSnapshot | null>(null);

	// BP-159 hot/cold-split mirrors. The delta (event) path reads these
	// instead of re-fetching: the cold snapshot (config + model-status,
	// which a dictation cannot change) plus the last sample / count /
	// corrections to prepend onto. Updated on every successful full
	// refresh alongside the state setters above.
	const sampleRef = useRef<HistoryRecord[]>([]);
	const totalCountRef = useRef<number | null>(null);
	const correctionUsageRef = useRef<CorrectionUsageSnapshot | null>(null);
	const coldRef = useRef<{
		cfg: VoiceTyperConfig | null;
		modelStatus: ModelStatusMap;
	} | null>(null);

	/**
	 * Fetch ALL dashboard data from the Python backend (cold path).
	 *
	 * Serves mount + manual refresh + the `config_changed` event (the
	 * only event that can move config / model-install / backend-status
	 * state). The per-dictation events (`transcription_final` /
	 * `history_changed`) use {@link refreshDataDelta} instead.
	 */
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const refreshData = useCallback(async () => {
		try {
			const [cfg, history, totalCount, status, correctionUsage, modelStatus] =
				await Promise.all([
					callRef.current<VoiceTyperConfig>("get_config"),
					callRef
						.current<HistoryRecord[]>("get_history", {
							limit: DASHBOARD_SAMPLE_LIMIT,
						})
						.catch(() => [] as HistoryRecord[]),
					// Fetch the TRUE total dictation count via the dedicated
					// `get_history_count` IPC. The `get_history` sample above
					// is still used for daily-activity / streak / period
					// computation (a 500-row sample covers weeks of use), but
					// the "Total Dictations" stat card reflects the actual
					// row count instead of capping at the sample forever.
					callRef.current<{ count: number }>("get_history_count").catch(() => ({
						count: 0,
					})),
					// Fetch the backend config directory (for the data-path display).
					// Returns null on failure, the caller falls back to the default path.
					callRef
						.current<{ config_dir?: string } | null>("get_status")
						.catch(() => null),
					// Per-correction usage snapshot (counts + per-day
					// correction/dictation totals) powering the
					// corrections-applied card. Null on failure, the
					// card then shows an empty state instead of blocking
					// the rest of the dashboard.
					callRef
						.current<CorrectionUsageSnapshot | null>("get_correction_usage")
						.catch(() => null),
					// MODEL-STATE fix: the "Current Setup" model/device
					// values must reflect ACTUAL install state, not the
					// config values (the app has no concrete default
					// model; ``device`` is a preference). ``get_model_status`` stats the
					// filesystem, the same truth the Models page and the
					// backend's startup banner use. A configured model
					// whose weights are not on disk is reported as "no
					// model selected", never as a live selection. Empty
					// map on failure → treated as nothing installed
					// (fail-safe: never advertise a model we can't
					// verify).
					callRef.current<ModelStatusMap>("get_model_status").catch(() => ({})),
				]);

			const recs = history ?? [];
			const newData = buildDashboardData({
				cfg: cfg ?? null,
				recs,
				// Prefer the dedicated count endpoint; fall back to the
				// sampled-history length only when the endpoint is
				// unavailable (e.g. older backend that doesn't expose
				// `get_history_count` yet). `totalCount?.count` is 0 on
				// both empty-DB and IPC-failure, the empty-DB case is
				// correct, and the IPC-failure case surfaces a 0 stat.
				totalCount: totalCount?.count ?? recs.length,
				modelStatus: modelStatus ?? {},
			});
			// SWR write-through, the next dashboard visit seeds from
			// this snapshot instead of flashing empty.
			writeIpcCache(DASHBOARD_CACHE_KEY, newData);
			setData(newData);
			setSample(recs);
			sampleRef.current = recs;
			totalCountRef.current = newData.totalCount;
			setCorrectionUsage(correctionUsage ?? null);
			correctionUsageRef.current = correctionUsage ?? null;
			setConfigRaw(cfg ?? null);
			// BP-159 cold snapshot: the delta path reuses these instead
			// of re-fetching config / model-status per dictation.
			coldRef.current = { cfg: cfg ?? null, modelStatus: modelStatus ?? {} };
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
			console.error(
				"[renderer:useDashboardData] Dashboard refresh failed:",
				err,
			);
			toast.error(message);
			setFetchError(message);
		} finally {
			// F4: bump the "last updated" timestamp after each refresh
			// attempt (success or failure) so the indicator stays accurate.
			markUpdatedRef.current();
		}
	}, []);

	/**
	 * Hot-path delta refresh for per-dictation events (BP-159).
	 *
	 * Fetches only `get_history({limit: DASHBOARD_DELTA_LIMIT})` +
	 * `get_history_count()` + `get_correction_usage()` and prepends the
	 * single new row onto the cached sample (capped at
	 * `DASHBOARD_SAMPLE_LIMIT`), reusing the cold snapshot for config /
	 * model-status / config-dir. Returns `true` when the delta applied;
	 * ANY deviation (no cold snapshot yet, count jump ≠ +1, empty or
	 * malformed head, head id already present, any throw) returns
	 * `false` so the caller falls through to the full {@link refreshData}
	 *, the worst case of the new code is byte-identical to the old one.
	 */
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef/markUpdatedRef are useLatestRef mirrors (see refreshData above); the *Ref mirrors below are refs by design
	const refreshDataDelta = useCallback(async (): Promise<boolean> => {
		try {
			const cold = coldRef.current;
			const prevSample = sampleRef.current;
			const prevCount = totalCountRef.current;
			if (!cold || prevCount === null) return false;
			const [head, countResp, correctionDelta] = await Promise.all([
				callRef
					.current<HistoryRecord[]>("get_history", {
						limit: DASHBOARD_DELTA_LIMIT,
					})
					.catch(() => null),
				callRef
					.current<{ count: number }>("get_history_count")
					.catch(() => null),
				callRef
					.current<CorrectionUsageSnapshot | null>("get_correction_usage")
					.catch(() => null),
			]);
			const nextCount = countResp?.count;
			// Exactly one new row since the last refresh, anything else
			// (import/restore/clear, concurrent writers, failed count
			// fetch) needs the full path.
			if (typeof nextCount !== "number" || nextCount !== prevCount + 1)
				return false;
			if (!Array.isArray(head) || head.length === 0) return false;
			const first = head[0];
			if (!first || typeof first.id !== "number") return false;
			// Head id already in the sample (duplicate/out-of-order
			// delivery), the full path re-syncs authoritatively.
			if (prevSample.some((r) => r.id === first.id)) return false;
			const recs = [first, ...prevSample].slice(0, DASHBOARD_SAMPLE_LIMIT);
			// A failed corrections fetch (null) keeps the previous
			// snapshot instead of blanking the card, the history delta
			// itself is still valid.
			const nextCorrections = correctionDelta ?? correctionUsageRef.current;
			const newData = buildDashboardData({
				cfg: cold.cfg,
				recs,
				totalCount: nextCount,
				modelStatus: cold.modelStatus,
			});
			writeIpcCache(DASHBOARD_CACHE_KEY, newData);
			setData(newData);
			setSample(recs);
			sampleRef.current = recs;
			totalCountRef.current = nextCount;
			setCorrectionUsage(nextCorrections);
			correctionUsageRef.current = nextCorrections;
			setFetchError(null);
			return true;
		} catch {
			return false;
		} finally {
			markUpdatedRef.current();
		}
	}, []);

	// ── Range-aware derived stats (single source: `sample`) ───────────
	const period = useMemo(
		() => computePeriodStats(sample, range),
		[sample, range],
	);
	const activity = useMemo(
		() => buildActivityBars(sample, range),
		[sample, range],
	);
	const correctionStats = useMemo(
		() => computeCorrectionStats(correctionUsage, range),
		[correctionUsage, range],
	);

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	// Wraps `refreshData()` so we can flip a `refreshing` flag for the
	// button's spinner state without disturbing the page's main
	// loading state (which is unused in Dashboard, the page renders
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

	// stale-data flag. Set to `true` when a `transcription_final`,
	// `history_changed`, or `config_changed` event arrives while the
	// window is hidden (document.visibilityState !== "visible"). The
	// visibilitychange listener below checks this flag on focus and
	// triggers a single debounced refresh, so background events don't
	// fire IPCs (delta: 3 cheap calls; full: 6 incl. the 500-row sample)
	// while the user isn't looking at the page. The next focus
	// collapses the backlog into ONE fetch.
	const staleRef = useRef(false);

	// Shared debounced scheduler for event-triggered refreshes. Keeps
	// the 500 ms debounce + the stale-while-hidden flag: background
	// events collapse into ONE fetch on focus instead of firing IPCs
	// while the user isn't looking.
	const scheduleDebouncedRefresh = useCallback(
		(fn: () => Promise<void>): (() => void) | undefined => {
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
			refreshTimer.current = setTimeout(fn, 500);
			return undefined;
		},
		[],
	);

	// F11-FIX (b-review Finding 11): invalidate the cached dashboard data
	// when history changes through a path OUTSIDE this page (clear/delete/
	// restore/favorite from the tray menu, another window, or a CLI tool).
	// Mirrors the transcription_final refresh. Both subscriptions share
	// the same debounced-refresh callback.
	//
	// BP-159: the hot path tries the cheap delta first (10-row head +
	// count + corrections, no config/model-status/status re-fetch) and
	// falls through to the full refresh on any deviation, per-dictation
	// cost no longer scales with history size.
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		return scheduleDebouncedRefresh(async () => {
			const applied = await refreshDataDelta();
			if (!applied) await refreshData();
		});
	}, [refreshData, refreshDataDelta, scheduleDebouncedRefresh]);

	// BP-159: config / model-install / backend-status state only moves on
	// `config_changed` (mount + manual refresh cover the rest), so the
	// cold getters re-fire here, via the full refresh, and nowhere else.
	const debouncedRefreshFullFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		return scheduleDebouncedRefresh(() => refreshData());
	}, [refreshData, scheduleDebouncedRefresh]);

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
	usePythonEvent("config_changed", debouncedRefreshFullFromEvent);

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
		range,
		setRange,
		period,
		activity,
		correctionStats,
		refreshData,
		handleManualRefresh,
		debouncedRefreshFromEvent,
		agoLabel,
		fetchError,
	};
}
