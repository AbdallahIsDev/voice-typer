//data-fetch + refresh + event-subscription hook extracted
// from `pages/Dashboard.tsx` (lines ~243-402 of the pre-split file).
// Owns the dashboard's `data` / `configRaw` / `refreshing` React state,
// the `refreshData` fetch, the manual-refresh wrapper, and the two
// `usePythonEvent` subscriptions (`transcription_final` and
// `history_changed`) that debounced-refresh the dashboard after backend
// state changes. Also owns the cleanup effect that clears the pending
// debounced-refresh timer on unmount.
// SINGLE-SOURCE-OF-TRUTH change (data-consistency fix):
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
// Behaviour is otherwise identical to the pre-split inline
// removed, `refreshData` is called directly at both former `loadData`
// call sites (initial mount + manual refresh).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { fetchSharedModelStatus } from "@/hooks/models/modelStatusCache";
import { useDebouncedCallback } from "@/hooks/useDebounce";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePythonEvent } from "@/hooks/usePython";
import { t } from "@/i18n/i18n";
import { peekIpcCache, writeIpcCache } from "@/lib/ipcCache";
import {
	MOUNT_RERACE_FAST_WINDOW_MS,
	type PrefetchCall,
} from "@/lib/snapshotCache";
import { resolveActiveModel } from "@/lib/utils/models";
import { useAnalyticsRange } from "@/stores/useAnalyticsRange";
import type { LausuConfig } from "@/types/config";
import type { HistoryRecord, ModelStatusMap } from "@/types/ipc";
import { formatWindowLabel } from "../lib/format";
import { buildDictationHeatmap, type DictationHeatmap } from "../lib/heatmap";
import {
	type ActivityChartData,
	buildActivityBars,
	type CorrectionStats,
	type CorrectionUsageSnapshot,
	type CustomWindow,
	computeCorrectionStats,
	computePeriodStats,
	computeStreaks,
	type DashboardData,
	dateKey,
	type PeriodStats,
	type RangeId,
	resolveRangeWindow,
	utcBoundsForWindow,
} from "../lib/streaks";

/** History sample size for the dashboard's derived stats. */
export const DASHBOARD_SAMPLE_LIMIT = 500;

export const DASHBOARD_DELTA_LIMIT = 10;

/**
 * Custom-window page fetch: cursor through `[startTs, endTs)` newest-first.
 * Caps at 10 pages (5000 rows): beyond that the window is served sampled
 * and the page footnotes it. A mid-loop failure keeps the rows fetched so
 * far (marked capped); a total failure throws for the caller's error path.
 */
export const CUSTOM_WINDOW_PAGE_LIMIT = 500;
export const CUSTOM_WINDOW_MAX_PAGES = 10;

type WindowCall = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

async function fetchWindowRecords(
	call: WindowCall,
	startTs: string,
	endTs: string,
): Promise<{ recs: HistoryRecord[]; capped: boolean }> {
	const out: HistoryRecord[] = [];
	let cursor: { timestamp: string; id: number } | null = null;
	for (let page = 0; page < CUSTOM_WINDOW_MAX_PAGES; page++) {
		const data: Record<string, unknown> = {
			limit: CUSTOM_WINDOW_PAGE_LIMIT,
			start_ts: startTs,
			end_ts: endTs,
		};
		if (cursor) {
			data.before_timestamp = cursor.timestamp;
			data.before_id = cursor.id;
		}
		let rows: HistoryRecord[] | null = null;
		try {
			const fetched = await call<HistoryRecord[]>("get_history", data);
			rows = Array.isArray(fetched) ? fetched : [];
		} catch (err) {
			// Total failure surfaces on the caller's error path; a partial
			// page keeps what it has and marks the window sampled.
			if (out.length === 0) throw err;
			return { recs: out, capped: true };
		}
		if (rows.length === 0) return { recs: out, capped: false };
		out.push(...rows);
		if (rows.length < CUSTOM_WINDOW_PAGE_LIMIT)
			return { recs: out, capped: false };
		const last = rows[rows.length - 1];
		// Unpageable tail (caller contract guarantees both fields):
		// stop and mark sampled rather than looping forever.
		if (
			!last ||
			typeof last.timestamp !== "string" ||
			typeof last.id !== "number"
		)
			return { recs: out, capped: true };
		cursor = { timestamp: last.timestamp, id: last.id };
	}
	return { recs: out, capped: true };
}

function buildDashboardData(args: {
	cfg: LausuConfig | null;
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
	let totalWords = 0,
		totalDuration = 0;
	for (const r of recs) {
		totalWords += r.word_count ?? 0;
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
		totalWords,
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

// Module-cache keys for the SWR seed (see lib/ipcCache.ts). The
// snapshot holds the RAW fetch inputs (sample + config + corrections),
// not just the derived DashboardData: period/activity/corrections memos
// derive from the sample, so caching data alone re-rendered zeros on
// revisit (C-CACHE-1). Legacy data-only key kept as a fallback reader.
const DASHBOARD_CACHE_KEY = "analytics.dashboardData";
const DASHBOARD_SNAPSHOT_CACHE_KEY = "analytics.dashboardSnapshot";
// Freshness window (TanStack staleTime equivalent): a revisit or hover
// inside this window reuses the snapshot with no IPC at all.
export const DASHBOARD_CACHE_TTL_MS = 30_000;

export interface DashboardSnapshot {
	data: DashboardData;
	sample: HistoryRecord[];
	configRaw: LausuConfig | null;
	correctionUsage: CorrectionUsageSnapshot | null;
	modelStatus: ModelStatusMap;
	fetchedAt: number;
}

function readSnapshot(): DashboardSnapshot | null {
	const snap = peekIpcCache<DashboardSnapshot>(DASHBOARD_SNAPSHOT_CACHE_KEY);
	if (snap?.data && Array.isArray(snap.sample)) return snap;
	const legacy = peekIpcCache<DashboardData>(DASHBOARD_CACHE_KEY);
	if (legacy)
		return {
			data: legacy,
			sample: [],
			configRaw: null,
			correctionUsage: null,
			modelStatus: {},
			fetchedAt: 0,
		};
	return null;
}

function isSnapshotFresh(snap: DashboardSnapshot | null): boolean {
	return (
		snap !== null &&
		snap.fetchedAt > 0 &&
		Date.now() - snap.fetchedAt < DASHBOARD_CACHE_TTL_MS
	);
}

function writeSnapshot(snap: DashboardSnapshot): void {
	writeIpcCache(DASHBOARD_SNAPSHOT_CACHE_KEY, snap);
	writeIpcCache(DASHBOARD_CACHE_KEY, snap.data);
}

// Hover-prefetch flight shared with the mount effect below: a click
// landing mid-prefetch piggybacks instead of firing a duplicate set.
let dashboardPrefetchInFlight: Promise<void> | null = null;

/** In-flight hover prefetch, if any (mount awaits it before deciding). */
export function peekDashboardPrefetch(): Promise<void> | null {
	return dashboardPrefetchInFlight;
}

export type { PrefetchCall } from "@/lib/snapshotCache";

// Hover/focus data prefetch (prefetchQuery equivalent): warms the
// snapshot cache before navigation. Fresh cache or an in-flight run
// short-circuits, so repeated hovers cost nothing (C-CACHE-4).
// Best-effort: failures leave the cache untouched and the mount fetch
// stays authoritative. Writes only on full success (validated shapes),
// a partial snapshot would lie to the fresh-cache fast path.
export function prefetchDashboardData(call: PrefetchCall): Promise<void> {
	if (isSnapshotFresh(readSnapshot())) return Promise.resolve();
	if (dashboardPrefetchInFlight) return dashboardPrefetchInFlight;
	dashboardPrefetchInFlight = (async () => {
		try {
			const [cfg, history, totalCount, correctionUsage, modelStatus] =
				await Promise.all([
					call("get_config"),
					call("get_history", { limit: DASHBOARD_SAMPLE_LIMIT }),
					call("get_history_count"),
					call("get_correction_usage"),
					// Shared snapshot: warms the Models page's cache too.
					fetchSharedModelStatus(call),
				]);
			if (!Array.isArray(history)) return;
			if (!cfg || typeof cfg !== "object" || !("model_size" in cfg)) return;
			const recs = history as HistoryRecord[];
			const count =
				totalCount &&
				typeof totalCount === "object" &&
				typeof (totalCount as { count?: unknown }).count === "number"
					? (totalCount as { count: number }).count
					: recs.length;
			const corrections =
				correctionUsage &&
				typeof correctionUsage === "object" &&
				"entries" in (correctionUsage as Record<string, unknown>)
					? (correctionUsage as CorrectionUsageSnapshot)
					: null;
			// Already validated by the shared fetch (null on failure).
			const status: ModelStatusMap = modelStatus ?? {};
			const data = buildDashboardData({
				cfg: cfg as LausuConfig,
				recs,
				totalCount: count,
				modelStatus: status,
			});
			writeSnapshot({
				data,
				sample: recs,
				configRaw: cfg as LausuConfig,
				correctionUsage: corrections,
				modelStatus: status,
				fetchedAt: Date.now(),
			});
		} catch {
			// Swallow: hover must never surface errors.
		} finally {
			dashboardPrefetchInFlight = null;
		}
	})();
	return dashboardPrefetchInFlight;
}

export interface UseDashboardDataArgs {
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>;
}

export interface UseDashboardDataResult {
	data: DashboardData | null;
	configRaw: LausuConfig | null;
	refreshing: boolean;
	/** Selected analytics time range ("Today" / "7 Days" / …). Owned by
	 *  `stores/useAnalyticsRange` because the control that changes it
	 *  lives in the title bar, outside this page's tree. */
	range: RangeId;
	/** Range-aware stats (current window + previous window for trends). */
	period: PeriodStats;
	/** Range-aware chart bars (hourly for Today, daily otherwise). */
	activity: ActivityChartData;
	/**
	 * Contribution grid for the heatmap card. NOT range-aware on purpose:
	 * it always spans the whole history the sample covers (capped at a
	 * year), because a per-day grid only reads at a scale of months.
	 */
	heatmap: DictationHeatmap;
	/** Range-aware corrections-applied totals from the vocabulary usage snapshot. */
	correctionStats: CorrectionStats;
	/**
	 * Custom window (day keys) backing `range === "custom"`, null
	 * otherwise. The window fetch covers the previous same-length span
	 * too, so trends have a denominator.
	 */
	customWindow: CustomWindow | null;
	/** The custom sample belongs to the current window (a switch
	 *  desyncs it until the refetch lands; memos read empty meanwhile). */
	customReady: boolean;
	/** Custom fetch in flight (switching ranges shows the skeleton). */
	customLoading: boolean;
	/** Custom window hit the page cap: stats are sampled, footnoted. */
	customCapped: boolean;
	/** Pre-formatted custom span for the chart subtitle ("Oct 1 – Oct 9"). */
	customWindowLabel?: string;
	/** Full refresh. Resolves true on success, false when the error path ran. */
	refreshData: () => Promise<boolean>;
	handleManualRefresh: () => Promise<void>;
	debouncedRefreshFromEvent: () => (() => void) | undefined;
	/** "Last updated" relative label (e.g. "5s ago") for the indicator. */
	agoLabel: string;
	/** Error from the most recent `refreshData()` call, or `null` if the last call succeeded or hasn't been called yet. */
	fetchError: string | null;
}

export function useDashboardData({
	call,
}: UseDashboardDataArgs): UseDashboardDataResult {
	// SWR seed: initial state reads the MODULE-level snapshot cache
	// (survives page unmount, so a revisit first-paints cached content).
	// Seeded as ONE snapshot so data + sample + config + corrections
	// stay consistent with each other (C-CACHE-1).

	// Ref mirror of `call` so `refreshData` keeps a STABLE identity
	// ([] deps). `call` is useCallback-stable in production, but test
	// mocks return a FRESH call per render, an identity churn would
	// re-fire the mount-load effect (refreshData → setData → re-render
	// → new call → loop → worker OOM). Same pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);

	const [initialSnapshot] = useState<DashboardSnapshot | null>(readSnapshot);
	const [data, setData] = useState<DashboardData | null>(
		() => initialSnapshot?.data ?? null,
	);
	// Ref mirror of `data` so the mount effect can hydrate state when a
	// hover prefetch lands AFTER this mount's first paint (seed was null
	// then, snapshot is fresh now): without it the mount would skip its
	// fetch and stay empty forever.
	const dataRef = useRef<DashboardData | null>(null);
	dataRef.current = data;
	// R7-F18: removed dead `const [, setLoading] = useState(true)`.
	const [configRaw, setConfigRaw] = useState<LausuConfig | null>(
		() => initialSnapshot?.configRaw ?? null,
	);
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

	// Selected time range, drives the stat cards + chart together. Read
	// from the shared store, not `useState`: the title-bar control that
	// changes it is a sibling tree, and the value must survive leaving
	// and re-entering the page.
	const range = useAnalyticsRange((s) => s.range);
	const customWindow = useAnalyticsRange((s) => s.customWindow);

	// The history sample backing every derived stat (kept so period /
	// activity memos recompute when the data refreshes). Seeded from
	// the snapshot: an empty seed is what flashed zeros on revisit.
	const [sample, setSample] = useState<HistoryRecord[]>(
		() => initialSnapshot?.sample ?? [],
	);
	// Custom-window records: fetched on demand (paged, newest-first)
	// instead of riding the 500-row sample, which may not cover an old
	// window at all. Event-delta refreshes leave it alone (historical
	// windows barely move); range switches + manual refresh re-fetch.
	const [customSample, setCustomSample] = useState<HistoryRecord[]>([]);
	// "startKey:endKey" the sample above was fetched for. A window switch
	// desyncs it until the refetch lands; memos treat a desynced sample
	// as empty so the page never shows one window's rows under another
	// window's label.
	const [customSampleKey, setCustomSampleKey] = useState<string | null>(null);
	const [customCapped, setCustomCapped] = useState(false);
	const [customLoading, setCustomLoading] = useState(false);
	// Per-correction usage snapshot from `get_correction_usage`.
	const [correctionUsage, setCorrectionUsage] =
		useState<CorrectionUsageSnapshot | null>(
			() => initialSnapshot?.correctionUsage ?? null,
		);

	// BP-159 hot/cold-split mirrors. The delta (event) path reads these
	// instead of re-fetching: the cold snapshot (config + model-status,
	// which a dictation cannot change) plus the last sample / count /
	// corrections to prepend onto. Updated on every successful full
	// refresh alongside the state setters above. Seeded from the cache
	// snapshot so the delta path works immediately on revisit.
	const sampleRef = useRef<HistoryRecord[]>(initialSnapshot?.sample ?? []);
	const totalCountRef = useRef<number | null>(
		initialSnapshot?.data.totalCount ?? null,
	);
	const correctionUsageRef = useRef<CorrectionUsageSnapshot | null>(
		initialSnapshot?.correctionUsage ?? null,
	);
	const coldRef = useRef<{
		cfg: LausuConfig | null;
		modelStatus: ModelStatusMap;
	} | null>(
		initialSnapshot
			? {
					cfg: initialSnapshot.configRaw,
					modelStatus: initialSnapshot.modelStatus,
				}
			: null,
	);

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract, .current must NOT become a dep
	const refreshData = useCallback(async (): Promise<boolean> => {
		try {
			const [cfg, history, totalCount, correctionUsage, modelStatus] =
				await Promise.all([
					callRef.current<LausuConfig>("get_config"),
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
					// Per-correction usage snapshot (counts + per-day
					// correction/dictation totals) powering the
					// corrections-applied card. Null on failure, the
					// card then shows an empty state instead of blocking
					// the rest of the dashboard.
					callRef
						.current<CorrectionUsageSnapshot | null>("get_correction_usage")
						.catch(() => null),
					// MODEL-STATE fix: the model/device shown in the
					// share image must reflect ACTUAL install state,
					// not the config values (the app has no concrete
					// default model; ``device`` is a preference).
					// The stat rides the shared model-status snapshot:
					// a fresh cache (Models visit, hover) skips the
					// disk stat; concurrent Analytics + Models readers
					// share one flight. A configured model whose
					// weights are not on disk is reported as "no model
					// selected", never as a live selection. Empty map
					// on failure → treated as nothing installed
					// (fail-safe: never advertise a model we can't
					// verify).
					fetchSharedModelStatus((type, data) =>
						callRef.current(type, data),
					).then((s) => s ?? {}),
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
			// SWR write-through: the full snapshot (not just derived
			// data) so the next visit seeds every memo (C-CACHE-1).
			writeSnapshot({
				data: newData,
				sample: recs,
				configRaw: cfg ?? null,
				correctionUsage: correctionUsage ?? null,
				modelStatus: modelStatus ?? {},
				fetchedAt: Date.now(),
			});
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
			setFetchError(null);
			return true;
		} catch (err) {
			// Surface refresh failures to the user instead of
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
			return false;
		} finally {
			// F4: bump the "last updated" timestamp after each refresh
			// attempt (success or failure) so the indicator stays accurate.
			markUpdatedRef.current();
		}
	}, []);

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
			writeSnapshot({
				data: newData,
				sample: recs,
				configRaw: cold.cfg,
				correctionUsage: nextCorrections,
				modelStatus: cold.modelStatus,
				fetchedAt: Date.now(),
			});
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

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef/markUpdatedRef are useLatestRef mirrors (see refreshData above); the *Ref mirrors below are refs by design
	const refreshCustomWindow = useCallback(async () => {
		const live = useAnalyticsRange.getState();
		if (live.range !== "custom" || !live.customWindow) return;
		setCustomLoading(true);
		try {
			// Fetch from the previous window's start so trends resolve
			// from the same rows the memos filter (single round-trip set).
			const resolved = resolveRangeWindow(
				"custom",
				new Date(),
				live.customWindow,
			);
			const bounds = utcBoundsForWindow(
				resolved.prevStartKey ?? live.customWindow.startKey,
				live.customWindow.endKey,
			);
			const { recs, capped } = await fetchWindowRecords(
				callRef.current,
				bounds.startTs,
				bounds.endTs,
			);
			setCustomSample(recs);
			setCustomSampleKey(
				`${live.customWindow.startKey}:${live.customWindow.endKey}`,
			);
			setCustomCapped(capped);
			setFetchError(null);
		} catch (err) {
			const message = t("analytics.refreshFailed");
			console.error(
				"[renderer:useDashboardData] Custom window refresh failed:",
				err,
			);
			toast.error(message);
			setFetchError(message);
		} finally {
			setCustomLoading(false);
			markUpdatedRef.current();
		}
	}, []);

	// Custom windows refetch on switch + window change (mount included).
	// Preset ranges recompute from the existing sample, no fetch.
	useEffect(() => {
		if (range !== "custom" || !customWindow) return;
		void refreshCustomWindow();
	}, [range, customWindow, refreshCustomWindow]);

	// ── Range-aware derived stats (single source: `sample`) ───────────
	// A custom window reads its own fetch (desynced sample = empty, so a
	// window switch never shows stale rows under the new label).
	const customKey =
		range === "custom" && customWindow
			? `${customWindow.startKey}:${customWindow.endKey}`
			: null;
	const customReady = customKey !== null && customKey === customSampleKey;
	const activeRecords =
		range === "custom" ? (customReady ? customSample : []) : sample;
	const activeWindow =
		range === "custom" ? (customWindow ?? undefined) : undefined;
	const period = useMemo(
		() => computePeriodStats(activeRecords, range, undefined, activeWindow),
		[activeRecords, range, activeWindow],
	);
	const activity = useMemo(
		() => buildActivityBars(activeRecords, range, undefined, activeWindow),
		[activeRecords, range, activeWindow],
	);
	// Deliberately NOT keyed on `range` — see `UseDashboardDataResult.heatmap`.
	const heatmap = useMemo(() => buildDictationHeatmap(sample), [sample]);
	const correctionStats = useMemo(
		() =>
			computeCorrectionStats(correctionUsage, range, undefined, activeWindow),
		[correctionUsage, range, activeWindow],
	);
	const customWindowLabel =
		range === "custom" && customWindow
			? formatWindowLabel(customWindow.startKey, customWindow.endKey)
			: undefined;

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	// Wraps `refreshData()` so we can flip a `refreshing` flag for the
	// button's spinner state without disturbing the page's main
	// loading state (which is unused in Dashboard, the page renders
	// a full-page skeleton via `if (!data) return <DashboardSkeleton />`).
	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await refreshData();
			// Manual refresh covers the custom window too (the
			// range-change effect only fires on switches, not revisits).
			await refreshCustomWindow();
		} finally {
			setRefreshing(false);
		}
	}, [refreshData, refreshCustomWindow]);

	// ── Proactive background refresh after new transcriptions ────────
	// Shared 500ms debounce (one timer for both event paths) so rapid
	// events coalesce; unmount auto-cancels.

	// stale-data flag. Set to `true` when a `transcription_final`,
	// `history_changed`, or `config_changed` event arrives while the
	// window is hidden (document.visibilityState !== "visible"). The
	// visibilitychange listener below checks this flag on focus and
	// triggers a single debounced refresh, so background events don't
	// fire IPCs (delta: 3 cheap calls; full: 6 incl. the 500-row sample)
	// while the user isn't looking at the page. The next focus
	// collapses the backlog into ONE fetch.
	const staleRef = useRef(false);
	const { debounced: debouncedFn, cancel: cancelDebouncedRefresh } =
		useDebouncedCallback((fn: () => Promise<void>) => {
			void fn();
		}, 500);

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
			debouncedFn(fn);
			return undefined;
		},
		[debouncedFn],
	);

	// when history changes through a path OUTSIDE this page (clear/delete/
	// restore/favorite from the tray menu, another window, or a CLI tool).
	// Mirrors the transcription_final refresh. Both subscriptions share
	// the same debounced-refresh callback.
	// BP-159: the hot path tries the cheap delta first (10-row head +
	// count + corrections, no config/model-status/status re-fetch) and
	// falls through to the full refresh on any deviation, per-dictation
	// cost no longer scales with history size.
	const debouncedRefreshFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		return scheduleDebouncedRefresh(async () => {
			const applied = await refreshDataDelta();
			if (!applied) {
				await refreshData();
			}
		});
	}, [refreshData, refreshDataDelta, scheduleDebouncedRefresh]);

	// BP-159: config / model-install / backend-status state only moves on
	// `config_changed` (mount + manual refresh cover the rest), so the
	// cold getters re-fire here, via the full refresh, and nowhere else.
	const debouncedRefreshFullFromEvent = useCallback(():
		| (() => void)
		| undefined => {
		return scheduleDebouncedRefresh(async () => {
			await refreshData();
		});
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
			cancelDebouncedRefresh();
		};
	}, [cancelDebouncedRefresh]);

	// Cold-start storm: the bridge's retry budget is spent by its
	// first success, so a mount racing a still-booting backend fails
	// fast with no data and no recovery. One delayed re-race heals
	// that without manual Retry; a second failure keeps the error
	// screen (bounded: exactly one extra attempt, unmount cancels).
	// Fresh cache (recent visit or hover prefetch) skips the mount
	// fetch entirely: the seeded state above already renders it, and
	// revalidating would waste the IPCs the cache just saved
	// (C-CACHE-2). A hover still in flight is awaited first so the
	// mount piggybacks it instead of duplicating it.
	useEffect(() => {
		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | null = null;
		const startedAt = Date.now();
		void (peekDashboardPrefetch() ?? Promise.resolve()).then(() => {
			if (cancelled) return;
			const snap = readSnapshot();
			if (isSnapshotFresh(snap)) {
				// Prefetch landed after first paint: hydrate the state
				// the null seed could not provide (C-CACHE-2).
				if (snap && dataRef.current === null) {
					setData(snap.data);
					setSample(snap.sample);
					sampleRef.current = snap.sample;
					totalCountRef.current = snap.data.totalCount;
					setCorrectionUsage(snap.correctionUsage);
					correctionUsageRef.current = snap.correctionUsage;
					setConfigRaw(snap.configRaw);
					coldRef.current = {
						cfg: snap.configRaw,
						modelStatus: snap.modelStatus,
					};
				}
				return;
			}
			void refreshData().then((ok) => {
				// Re-race only fast failures (refusal before the backend
				// was up). A slow failure already had the bridge's
				// cold-start patience — doubling it delays the error
				// screen by another full wait (C-CACHE-10).
				if (
					!ok &&
					!cancelled &&
					Date.now() - startedAt < MOUNT_RERACE_FAST_WINDOW_MS
				) {
					timer = setTimeout(() => {
						if (!cancelled) void refreshData();
					}, 8000);
				}
			});
		});
		return () => {
			cancelled = true;
			if (timer !== null) clearTimeout(timer);
		};
	}, [refreshData]);

	return {
		data,
		configRaw,
		refreshing,
		range,
		period,
		activity,
		heatmap,
		correctionStats,
		customWindow,
		customReady,
		customLoading,
		customCapped,
		customWindowLabel,
		refreshData,
		handleManualRefresh,
		debouncedRefreshFromEvent,
		agoLabel,
		fetchError,
	};
}
