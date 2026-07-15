import {
	Activity03Icon,
	AiBrain03Icon,
	Calendar01Icon,
	File02Icon,
	LayoutGridIcon,
	Share08Icon,
	SpeechToTextIcon,
	Time02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { DashboardStatCard } from "@/components/dashboard/DashboardStatCard";
import { QuickInfoCard } from "@/components/dashboard/QuickInfoCard";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button.tsx";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { computeShareStats, useStatsShare } from "@/hooks/useStatsShare";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, Page, TodayStats } from "@/types/ipc";

// ── Module-level cache ────────────────────────────────────────────
let _cachedData: DashboardData | null = null;

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

/** Format seconds into a human-readable duration string. */
function formatDuration(seconds: number): string {
	if (seconds <= 0) return "0m";
	const totalMinutes = Math.round(seconds / 60);
	if (totalMinutes < 60) return `${totalMinutes}m`;
	const h = Math.floor(totalMinutes / 60);
	const m = totalMinutes % 60;
	return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/** Format number compactly (e.g., 1234 → "1.2K") */
function compactNumber(n: number): string {
	if (n >= 1000) {
		const k = n / 1000;
		const display = Math.floor(k * 10) / 10;
		return `${display}K`;
	}
	return String(n);
}

/** Determine the max bar height based on data range. */
function barHeight(count: number, max: number): number {
	if (max === 0) return 8;
	return Math.max(8, Math.round((count / max) * 64));
}

/** Parse a timestamp string to a YYYY-MM-DD date key. */
function dateKey(ts: string): string {
	try {
		return new Date(ts).toISOString().slice(0, 10);
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
		if (dateStr === today.toISOString().slice(0, 10))
			return t("analytics.today");
		if (dateStr === yesterday.toISOString().slice(0, 10))
			return t("analytics.yesterday");
		return dateStr.slice(5); // "MM-DD"
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
		const key = d.toISOString().slice(0, 10);
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

	const today = new Date().toISOString().slice(0, 10);
	const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);

	// Current streak (must include today or yesterday)
	let current = 0;
	if (sorted[0] === today || sorted[0] === yesterday) {
		for (let i = 0; i < sorted.length; i++) {
			const expected = new Date(Date.now() - i * 86400000)
				.toISOString()
				.slice(0, 10);
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

interface DashboardPageProps {
	onNavigate?: (page: Page) => void;
}

export default function DashboardPage({
	onNavigate: _onNavigate,
}: DashboardPageProps) {
	const { call } = usePython();
	const [data, setData] = useState<DashboardData | null>(_cachedData);
	const [, setLoading] = useState(true);
	const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null);
	// F4 (b-review Finding 11): "Last updated" indicator state. The
	// module-level `_cachedData` survives page navigations, so we mark
	// the timestamp after each successful refreshData() to surface
	// staleness to the user.
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const { imageRef, shareAsImage } = useStatsShare();

	/** Fetch all dashboard data from the Python backend. */
	const refreshData = useCallback(async () => {
		try {
			const [cfg, todayStats, history] = await Promise.all([
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
				totalCount: recs.length,
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
			_cachedData = newData;
			setData(newData);
			setConfigRaw(cfg ?? null);
		} catch {
			// Silently ignore — next load picks up fresh data
		} finally {
			// F4: bump the "last updated" timestamp after each refresh
			// attempt (success or failure) so the indicator stays accurate.
			markUpdated();
		}
	}, [call, markUpdated]);

	const loadData = useCallback(async () => {
		setLoading(true);
		await refreshData();
		setLoading(false);
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
		useCallback(() => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			refreshTimer.current = setTimeout(refreshData, 500);
		}, [refreshData]),
	);

	// F11-FIX (b-review Finding 11): invalidate the cached dashboard data
	// when history changes through a path OUTSIDE this page (clear/delete/
	// restore/favorite from the tray menu, another window, or a CLI tool).
	// Mirrors the transcription_final refresh below.
	usePythonEvent(
		"history_changed",
		useCallback(() => {
			if (refreshTimer.current) clearTimeout(refreshTimer.current);
			refreshTimer.current = setTimeout(refreshData, 500);
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

	if (!data) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// ── Render ───────────────────────────────────────────────────────

	const d = data;
	const maxCount = Math.max(1, ...d.dailyActivity.map((a) => a.count));

	const handleShare = () => shareAsImage("voice-typer-stats");

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
			<PageHeading
				title={t("analytics.title")}
				description={t("analytics.description")}
			>
				{data && configRaw && data.todayCount > 0 && (
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
			    refresh button. The module-level `_cachedData` survives page
			    navigations, so we surface staleness here. */}
			<div className="flex justify-end pb-2">
				<LastUpdatedIndicator
					agoLabel={agoLabel}
					onRefresh={handleManualRefresh}
					refreshing={refreshing}
				/>
			</div>

			<div className="space-y-8">
				{/* ── Today's Stats Grid ──────────────────────────────────── */}
				<div className="grid grid-cols-4 gap-3">
					<DashboardStatCard
						label={t("analytics.dictationsToday")}
						value={String(d.todayCount)}
						icon={SpeechToTextIcon}
						sublabel={t("analytics.charsValue", {
							count: d.todayChars.toLocaleString(),
						})}
					/>
					<DashboardStatCard
						label={t("analytics.recordingTime")}
						value={formatDuration(d.todayDuration)}
						icon={Time02Icon}
						sublabel={t("analytics.today")}
					/>
					<DashboardStatCard
						label={t("analytics.allTimeTotal")}
						value={compactNumber(d.totalCount)}
						icon={File02Icon}
						sublabel={t("analytics.charsValue", {
							count: d.totalChars.toLocaleString(),
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
					<div className="flex items-end justify-between gap-2 h-20">
						{d.dailyActivity.map((day) => (
							<div
								key={day.date}
								className="flex flex-1 flex-col items-center gap-2"
							>
								<span className="text-xs text-(--text-muted) font-medium tabular-nums">
									{day.count}
								</span>
								<div
									className="w-full max-w-10 rounded-sm bg-accent/60 transition-all duration-300"
									style={{ height: `${barHeight(day.count, maxCount)}px` }}
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
								/>
								<span className="text-[11px] text-(--text-muted)">
									{day.dayName}
								</span>
							</div>
						))}
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
