import {
	Activity03Icon,
	AiBrain03Icon,
	Calendar01Icon,
	File02Icon,
	LayoutGridIcon,
	Mic02Icon,
	Share08Icon,
	SpeechToTextIcon,
	Time02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { DashboardStatCard } from "@/components/dashboard/DashboardStatCard";
import { QuickInfoCard } from "@/components/dashboard/QuickInfoCard";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Button } from "@/components/ui/button.tsx";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import {
	canShareStats,
	computeShareStats,
	useStatsShare,
} from "@/hooks/useStatsShare";
import { getLocale, t } from "@/i18n/i18n";
import { compactNumber, formatDuration } from "@/lib/format";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, TodayStats } from "@/types/ipc";

// ── Module-level cache ────────────────────────────────────────────
// Previously this was a module-level `let _cachedData` mutable binding —
// global mutable state that leaked across HMR / test re-mounts and was
// not React-aware. The cache now lives in a component-scoped `useRef`
// (see `DashboardPage`); localStorage is NOT used here (the dashboard
// data is derived, not user-edited), so the ref is the only cache.
// The `DashboardData` interface is kept at module scope so the helper
// functions above can reference the type.

interface DashboardData {
	todayCount: number;
	todayChars: number;
	todayWordCount: number;
	todayDuration: number;
	totalCount: number;
	totalChars: number;
	totalDuration: number;
	favoritesCount: number;
	model: string;
	device: string;
	language: string;
	dailyActivity: {
		date: string;
		count: number;
		label: string;
		dayName: string;
	}[];
	currentStreak: number;
	maxStreak: number;
	activeDays: number;
}

// ``compactNumber`` and ``formatDuration`` are now
// imported from the shared ``lib/format.ts`` module so Dashboard and
// StatCards use the same locale-aware implementation.  The previous
// local copies hardcoded English suffixes (``"h"`` / ``"m"`` for
// durations, ``String(n)`` for sub-1000 counts) and ignored the
// user-selected UI locale.
//
// BG-9: ``formatDuration`` in particular now resolves the ``h`` / ``m``
// glyphs through ``t()`` (``analytics.durationHours`` /
// ``durationMinutes`` / ``durationHoursMinutes`` / ``durationZero``)
// so non-English locales see translated suffixes once F1 translates
// the new keys.

/** Determine the max bar height based on data range. */
function barHeight(count: number, max: number): number {
	if (max === 0) return 8;
	return Math.max(8, Math.round((count / max) * 64));
}

/** Format a Date as a YYYY-MM-DD string in LOCAL time (not UTC).
 *
 * CR-90: the previous implementation used
 * ``new Date(ts).toISOString().slice(0, 10)`` which formats the date in
 * UTC. For users in negative UTC offsets (the Americas, -05:00 to
 * -10:00), a transcription logged at 8pm local on Tuesday was bucketed
 * into Wednesday's UTC date — so the dashboard's "Today" total stayed
 * at zero until the next local day, and the 7-day activity chart
 * showed entries on the wrong bars. Switching to local-date keys keeps
 * the bucket aligned with the user's calendar day.
 */
function localDateKey(d: Date): string {
	const y = d.getFullYear();
	const m = String(d.getMonth() + 1).padStart(2, "0");
	const day = String(d.getDate()).padStart(2, "0");
	return `${y}-${m}-${day}`;
}

/** Parse a timestamp string to a YYYY-MM-DD date key (in LOCAL time). */
function dateKey(ts: string): string {
	try {
		return localDateKey(new Date(ts));
	} catch {
		return ts;
	}
}

/** Get day-of-week abbreviation for a date string. */
function dayAbbr(dateStr: string): string {
	const days = [
		t("analytics.days.sun"),
		t("analytics.days.mon"),
		t("analytics.days.tue"),
		t("analytics.days.wed"),
		t("analytics.days.thu"),
		t("analytics.days.fri"),
		t("analytics.days.sat"),
	];
	try {
		return days[new Date(dateStr).getDay()];
	} catch {
		return dateStr;
	}
}

/** Get a human-friendly label like "Today", "Yesterday", or the date. */
function dayLabel(dateStr: string): string {
	try {
		const today = new Date();
		const yesterday = new Date(today);
		yesterday.setDate(yesterday.getDate() - 1);
		// CR-90: use localDateKey (not toISOString().slice) so the
		// "Today" / "Yesterday" comparison honors the user's local
		// calendar day instead of UTC.
		if (dateStr === localDateKey(today)) return t("analytics.today");
		if (dateStr === localDateKey(yesterday)) return t("analytics.yesterday");
		// CR-46: format the MM-DD fallback in the user-selected UI
		// locale instead of slicing the ISO string (which is always
		// Gregorian/ASCII and ignores locale-aware month formatting).
		try {
			return new Intl.DateTimeFormat(getLocale(), {
				month: "short",
				day: "2-digit",
			}).format(new Date(dateStr));
		} catch {
			return dateStr.slice(5); // "MM-DD"
		}
	} catch {
		return dateStr;
	}
}

/** Build the 7-day activity array from a list of history records. */
function computeDailyActivity(
	records: HistoryRecord[],
): { date: string; count: number; label: string; dayName: string }[] {
	const counts = new Map<string, number>();
	for (const r of records) {
		const key = dateKey(r.timestamp);
		counts.set(key, (counts.get(key) ?? 0) + 1);
	}
	const result: {
		date: string;
		count: number;
		label: string;
		dayName: string;
	}[] = [];
	const now = new Date();
	for (let i = 6; i >= 0; i--) {
		const d = new Date(now);
		d.setDate(d.getDate() - i);
		// CR-90: use localDateKey (not toISOString().slice) so the
		// 7-day chart buckets honor the user's local calendar day.
		const key = localDateKey(d);
		result.push({
			date: key,
			count: counts.get(key) ?? 0,
			label: dayLabel(key),
			dayName: dayAbbr(key),
		});
	}
	return result;
}

/** Compute consecutive-day streak from history records. */
function computeStreaks(records: HistoryRecord[]): {
	current: number;
	max: number;
	activeDays: number;
} {
	const days = new Set<string>();
	for (const r of records) {
		days.add(dateKey(r.timestamp));
	}
	const sorted = Array.from(days).sort().reverse();
	if (sorted.length === 0) return { current: 0, max: 0, activeDays: 0 };

	// CR-90: use localDateKey (not toISOString().slice) so streak
	// calculations anchor on the user's local calendar day.
	const today = localDateKey(new Date());
	const yesterday = localDateKey(new Date(Date.now() - 86400000));

	// Current streak (must include today or yesterday)
	let current = 0;
	if (sorted[0] === today || sorted[0] === yesterday) {
		for (let i = 0; i < sorted.length; i++) {
			const expected = localDateKey(new Date(Date.now() - i * 86400000));
			if (sorted[i] === expected) current++;
			else break;
		}
	}

	// Max streak (scan all)
	let max = 1;
	let run = 1;
	for (let i = 1; i < sorted.length; i++) {
		const prev = new Date(sorted[i - 1]);
		const curr = new Date(sorted[i]);
		const diffMs = prev.getTime() - curr.getTime();
		if (diffMs <= 86400000 * 1.5) {
			run++;
			if (run > max) max = run;
		} else {
			run = 1;
		}
	}
	if (sorted.length === 1) max = 1;

	return { current, max, activeDays: sorted.length };
}

// ── Page Component ────────────────────────────────────────────────

// NOTE: App.tsx prop passing will be removed by EC-FIX-13.
// EC-FIX-14 (BACKLOG-004): DashboardPage now obtains `navigate` via the
// useNavigation hook directly, eliminating the `onNavigate` prop drill
// from App.tsx.
export default function DashboardPage() {
	// EC-FIX-14: obtain `navigate` directly from the navigation hook
	// instead of receiving it as an `onNavigate` prop from App.tsx.
	const { navigate } = useNavigation();
	const { call } = usePython();
	// Per-instance cache ref (replaced the prior module-level
	// `let _cachedData` mutable binding). The initial `useState` value
	// seeds from this ref so the first render after a navigation back
	// to the dashboard shows the previously-fetched data instead of
	// flashing empty (the ref is per-instance, so the cache only
	// survives while the component is mounted — but React Router
	// keeps the component mounted across sibling-page navigations
	// in the current route tree, which is the common case).
	const cachedDataRef = useRef<DashboardData | null>(null);
	const [data, setData] = useState<DashboardData | null>(cachedDataRef.current);
	// R7-F18: removed dead `const [, setLoading] = useState(true)`.
	const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null);
	// F4 (b-review Finding 11): "Last updated" indicator state. The
	// per-instance `cachedDataRef` survives re-renders within the same
	// mount, so we mark the timestamp after each successful refreshData()
	// to surface staleness to the user.
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const { imageRef, shareAsImage } = useStatsShare();

	/** Fetch all dashboard data from the Python backend. */
	const refreshData = useCallback(async () => {
		try {
			const [cfg, todayStats, history, totalCount] = await Promise.all([
				call<VoiceTyperConfig>("get_config"),
				call<TodayStats>("get_today_stats").catch(() => ({
					count: 0,
					chars: 0,
					word_count: 0,
					duration: 0,
				})),
				call<HistoryRecord[]>("get_history", { limit: 200 }).catch(
					() => [] as HistoryRecord[],
				), // NEW-IPC-004: capped at 200
				// Fetch the TRUE total dictation count via the dedicated
				// `get_history_count` IPC. The `get_history({limit: 200})`
				// sample above is still used for daily-activity / streak
				// computation (where 200 rows is a sufficient sample), but
				// the "Total Dictations" stat card now reflects the actual
				// row count instead of capping at 200 forever.
				call<{ count: number }>("get_history_count").catch(() => ({
					count: 0,
				})),
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
		} catch (err) {
			// Fix #9: surface refresh failures to the user instead of
			// silently swallowing them.  The previous implementation
			// caught and ignored ALL errors, so a backend disconnect
			// during a background refresh (e.g. transcription_final
			// trigger) left the user staring at stale data with no
			// indication that the refresh failed.  We now show a
			// toast.error so the user knows to retry manually via the
			// LastUpdatedIndicator refresh button.
			console.error("Dashboard refresh failed:", err);
			toast.error(t("analytics.refreshFailed"));
		} finally {
			// F4: bump the "last updated" timestamp after each refresh
			// attempt (success or failure) so the indicator stays accurate.
			markUpdated();
		}
	}, [call, markUpdated]);

	// R7-F18: removed `setLoading` calls — dead state variable.
	const loadData = useCallback(async () => {
		await refreshData();
	}, [refreshData]);

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	// Wraps `loadData()` so we can flip a `refreshing` flag for the
	// button's spinner state without disturbing the page's main
	// loading state (which is unused in Dashboard — the page renders
	// a full-page spinner via `if (!data) return <Spinner />`).
	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await loadData();
		} finally {
			setRefreshing(false);
		}
	}, [loadData]);

	// ── Proactive background refresh after new transcriptions ────────
	const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	usePythonEvent(
		"transcription_final",
		useCallback((): (() => void) | undefined => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			refreshTimer.current = setTimeout(refreshData, 500);
			return undefined;
		}, [refreshData]),
	);

	// F11-FIX (b-review Finding 11): invalidate the cached dashboard data
	// when history changes through a path OUTSIDE this page (clear/delete/
	// restore/favorite from the tray menu, another window, or a CLI tool).
	// Mirrors the transcription_final refresh below.
	usePythonEvent(
		"history_changed",
		useCallback((): (() => void) | undefined => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			refreshTimer.current = setTimeout(refreshData, 500);
			return undefined;
		}, [refreshData]),
	);

	useEffect(() => {
		return () => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
		};
	}, []);

	useEffect(() => {
		loadData();
	}, [loadData]);

	// ── Loading State ────────────────────────────────────────────────
	//
	// Fix #19: skeleton loading state.  The previous implementation
	// rendered a bare full-page Spinner when there was no cached data,
	// which caused a layout shift when the real data arrived (the
	// spinner was centred in the viewport; the real content renders
	// top-aligned with the 4-stat grid).  The skeleton mirrors the
	// final layout (heading + 4 stat cards + 7-day chart placeholder)
	// so the transition from "loading" to "loaded" is visually stable.
	// The skeleton is only shown on the FIRST load (when ``!data``) —
	// subsequent refreshes keep the previous data visible while the
	// new data loads (the ``refreshing`` flag drives the
	// LastUpdatedIndicator spinner instead).

	if (!data) {
		return (
			<section
				className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6"
				aria-label={t("analytics.loadingAria")}
				aria-busy="true"
			>
				{/* Heading skeleton */}
				<div className="space-y-2 pb-2">
					<div className="h-6 w-40 animate-pulse rounded bg-(--bg-subtle)" />
					<div className="h-4 w-64 animate-pulse rounded bg-(--bg-subtle)" />
				</div>
				<div className="flex justify-end pb-2">
					<div className="h-4 w-24 animate-pulse rounded bg-(--bg-subtle)" />
				</div>
				{/* 4 stat-card skeleton */}
				<div className="grid grid-cols-4 gap-3 mt-6">
					{[0, 1, 2, 3].map((i) => (
						<div
							key={`stat-skel-${i}`}
							className="rounded-xl border border-border bg-(--bg-subtle) p-5 flex flex-col items-center justify-center gap-2"
						>
							<div className="h-4 w-4 animate-pulse rounded bg-(--bg-subtle)" />
							<div className="h-6 w-12 animate-pulse rounded bg-(--bg-subtle)" />
							<div className="h-3 w-16 animate-pulse rounded bg-(--bg-subtle)" />
						</div>
					))}
				</div>
				{/* 7-day chart skeleton */}
				<div className="rounded-xl border border-border bg-(--bg-subtle) p-5 mt-8">
					<div className="flex items-center justify-between mb-5">
						<div className="space-y-1.5">
							<div className="h-4 w-32 animate-pulse rounded bg-(--bg-subtle)" />
							<div className="h-3 w-40 animate-pulse rounded bg-(--bg-subtle)" />
						</div>
						<div className="h-4 w-4 animate-pulse rounded bg-(--bg-subtle)" />
					</div>
					<div className="flex items-end justify-between gap-2 h-20">
						{[0, 1, 2, 3, 4, 5, 6].map((i) => (
							<div
								key={`bar-skel-${i}`}
								className="flex flex-1 flex-col items-center gap-2"
							>
								<div className="h-3 w-6 animate-pulse rounded bg-(--bg-subtle)" />
								<div
									className="w-full max-w-10 animate-pulse rounded-sm bg-(--bg-subtle)"
									style={{ height: `${20 + ((i * 7) % 40)}px` }}
								/>
								<div className="h-3 w-6 animate-pulse rounded bg-(--bg-subtle)" />
							</div>
						))}
					</div>
				</div>
				{/* Quick-info row skeleton */}
				<div className="grid grid-cols-3 gap-3 mt-8">
					{[0, 1, 2].map((i) => (
						<div
							key={`qi-skel-${i}`}
							className="rounded-lg border border-border bg-(--bg-subtle) p-3.5 flex items-center gap-3"
						>
							<div className="h-8 w-8 animate-pulse rounded-lg bg-(--bg-subtle)" />
							<div className="flex-1 space-y-1.5">
								<div className="h-3 w-12 animate-pulse rounded bg-(--bg-subtle)" />
								<div className="h-4 w-20 animate-pulse rounded bg-(--bg-subtle)" />
							</div>
						</div>
					))}
				</div>
			</section>
		);
	}

	// ── Render ───────────────────────────────────────────────────────

	const d = data;
	const maxCount = Math.max(1, ...d.dailyActivity.map((a) => a.count));

	// Fix #10: first-run empty state.  When the user has zero total
	// transcriptions (e.g. fresh install, never dictated), the dashboard
	// would otherwise show four zero stat cards + an empty 7-day chart
	// with no explanation.  We surface an EmptyState with a CTA to start
	// dictation instead — matching the History page's pattern.
	const isFirstRun = d.totalCount === 0;

	const handleShare = () => shareAsImage("voice-typer-stats");

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
			<PageHeading
				title={t("analytics.title")}
				description={t("analytics.description")}
			>
				{data &&
					configRaw &&
					canShareStats({
						todayCount: data.todayCount,
						totalCount: data.totalCount,
					}) && (
						<Button
							variant="outline"
							size="sm"
							onClick={handleShare}
							// FIX: muted text/icon by default, white on hover —
							// matches the muted style used by outline buttons
							// elsewhere (History action row, Templates add, etc.).
							className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						>
							<HugeiconsIcon
								icon={Share08Icon}
								strokeWidth={1.625}
								className="h-4 w-4 shrink-0"
							/>
							{t("home.shareStats")}
						</Button>
					)}
			</PageHeading>

			{/* F4 (b-review Finding 11): "Last updated" indicator + manual
			    refresh button. The per-instance `cachedDataRef` survives
			    re-renders within the same mount, so we surface staleness
			    here. */}
			<div className="flex justify-end pb-2">
				<LastUpdatedIndicator
					agoLabel={agoLabel}
					onRefresh={handleManualRefresh}
					refreshing={refreshing}
				/>
			</div>

			{/* Fix #10: first-run empty state.  When the user has never
			    dictated (totalCount === 0), show an EmptyState with a CTA
			    to start dictation instead of four zero-value stat cards
			    and an empty 7-day chart.  The CTA navigates to the Home
			    page (matches the History page's empty-state pattern). */}
			{isFirstRun ? (
				<EmptyState
					icon={Mic02Icon}
					title={t("analytics.noDataTitle")}
					description={t("analytics.noDataDescription")}
					actionLabel={t("analytics.startDictation")}
					actionIcon={SpeechToTextIcon}
					onAction={() => navigate("home")}
				/>
			) : (
				<div className="space-y-8">
					{/* ── Today's Stats Grid ──────────────────────────────────── */}
					<div className="grid grid-cols-4 gap-3">
						<DashboardStatCard
							label={t("analytics.dictationsToday")}
							value={String(d.todayCount)}
							icon={SpeechToTextIcon}
							sublabel={t("analytics.charsValue", {
								count: d.todayChars.toLocaleString(getLocale()),
							})}
						/>
						<DashboardStatCard
							label={t("analytics.recordingTime")}
							value={formatDuration(d.todayDuration)}
							icon={Time02Icon}
							sublabel={t("analytics.today")}
						/>
						<DashboardStatCard
							label={t("analytics.recentTotal")}
							value={compactNumber(d.totalCount)}
							icon={File02Icon}
							sublabel={t("analytics.charsValue", {
								count: d.totalChars.toLocaleString(getLocale()),
							})}
						/>
						<DashboardStatCard
							label={t("analytics.activeDays")}
							value={String(d.activeDays)}
							icon={Calendar01Icon}
							sublabel={
								d.currentStreak > 0
									? t("analytics.dayStreak", { count: String(d.currentStreak) })
									: t("analytics.noStreak")
							}
						/>
					</div>

					{/* ── 7-Day Activity Bar Chart ──────────────────────────────── */}
					<div className="rounded-xl border border-border bg-(--bg-subtle) p-5">
						<div className="flex items-center justify-between mb-5">
							<div className="space-y-0.5">
								<h2 className="font-sans text-sm font-semibold text-(--text-primary)">
									{t("analytics.sevenDayActivity")}
								</h2>
								<p className="text-xs text-(--text-muted)">
									{t("analytics.transcriptionsPerDay")}
								</p>
							</div>
							<HugeiconsIcon
								icon={Activity03Icon}
								strokeWidth={1.625}
								className="h-4 w-4 text-(--text-muted)"
							/>
						</div>
						<div
							className="flex items-end justify-between gap-2 h-20"
							role="img"
							aria-label={t("analytics.sevenDayActivityChartAria", {
								counts: d.dailyActivity
									.map((a) => `${a.label}: ${a.count}`)
									.join(", "),
							})}
						>
							{d.dailyActivity.map((day) => {
								// BG-3: the 7-day chart is informational, not
								// interactive. Wrapping each bar in a <button>
								// produced 7 dead-end tab stops and an SR
								// announcement of "button, button, ..." with no
								// chart context. We now expose the entire chart
								// to AT as a single role="img" with a
								// descriptive aria-label (set on the container
								// above) and render each bar as a non-interactive
								// <div> with a title attribute for the mouse
								// hover tooltip (title is not announced by SRs
								// but is available to sighted mouse users).
								// Bumped bar opacity from /60 to /80 for
								// WCAG 1.4.11 contrast against adjacent
								// backgrounds.
								return (
									<div
										key={day.date}
										className="flex flex-1 flex-col items-center gap-2"
									>
										<span className="text-xs text-(--text-muted) font-medium tabular-nums">
											{day.count}
										</span>
										<div
											title={
												day.count === 1
													? t("analytics.dayCountTooltipSingular", {
															label: day.label,
															count: String(day.count),
														})
													: t("analytics.dayCountTooltipPlural", {
															label: day.label,
															count: String(day.count),
														})
											}
											className="w-full max-w-10 rounded-sm bg-accent/80 transition-all duration-300"
											style={{ height: `${barHeight(day.count, maxCount)}px` }}
										/>
										<span className="text-[11px] text-(--text-muted)">
											{day.dayName}
										</span>
									</div>
								);
							})}
						</div>
					</div>

					{/* ── Quick Stats Bar ──────────────────────────────────────── */}
					<div className="grid grid-cols-3 gap-3">
						<QuickInfoCard
							icon={AiBrain03Icon}
							label={t("analytics.model")}
							value={d.model}
						/>
						<QuickInfoCard
							icon={LayoutGridIcon}
							label={t("analytics.device")}
							value={d.device.toUpperCase()}
						/>
						<QuickInfoCard
							icon={Activity03Icon}
							label={t("analytics.language")}
							value={d.language}
						/>
					</div>

					{/* Data path */}
					<p className="text-xs text-(--text-muted) text-center pb-4">
						{t("analytics.dataPath")}
					</p>
				</div>
			)}

			{/* ── Hidden share image capture target ──────────────── */}
			{/* EXPORT-FIX: removed clipPath:inset(50%) —
			    html-to-image copied it onto the cloned node and
			    clipped the PNG to 0×0. See Home.tsx for full
			    rationale. The toPng style override (clipPath:none)
			    is the primary defense; removing clipPath here
			    eliminates the footgun. */}
			<div
				ref={imageRef}
				aria-hidden
				style={{
					position: "absolute",
					top: 0,
					left: 0,
					zIndex: -100,
					pointerEvents: "none",
				}}
			>
				{data && configRaw && (
					<StatsShareImage
						stats={computeShareStats(
							{
								count: data.todayCount,
								chars: data.todayChars,
								word_count: data.todayWordCount,
								duration: data.todayDuration,
							},
							configRaw.asr_backend,
						)}
					/>
				)}
			</div>
		</div>
	);
}
