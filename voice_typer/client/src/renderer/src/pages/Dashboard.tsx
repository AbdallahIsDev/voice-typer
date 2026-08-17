//thin composition root. Data-fetch / refresh / event-subscription
// lives in `./dashboard/hooks/useDashboardData`; pure helpers in
// `./dashboard/lib/{streaks,format}`; presentational sub-components in
// `./dashboard/components/`. LOC history: 732 (pre-split) → <150 (post-split).
//
// Analytics layout:
//   1. Range selector (Today / 7 Days / 30 Days / All Time) — drives
//      the stat cards AND the chart together (single source: one
//      history sample, UTC-correct day bucketing — see the hook).
//   2. Four range-aware stat cards with trend indicators vs the
//      previous period of the same length.
//   3. The activity chart (hourly for Today, daily otherwise) with a
//      y-axis, gridlines, and zero-vs-no-data distinction.
//   4. Derived-metric highlights (avg chars, longest session, peak
//      weekday) — only metrics the data actually supports.
//   5. A visually demoted "Current Setup" section (Model / Device /
//      Language) so system/config info doesn't compete with usage
//      metrics for attention.

import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import {
    DashboardStatCard,
    type StatTrend,
} from "@/components/dashboard/DashboardStatCard";
import { QuickInfoCard } from "@/components/dashboard/QuickInfoCard";
import { ShareStatsDialog } from "@/components/dashboard/ShareStatsDialog";
import { formatCompactNumber } from "@/components/dashboard/StatCards";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { EmptyState } from "@/components/feedback/EmptyState";
import {
    AiBrain03Icon,
    AlertCircleIcon,
    Calendar01Icon,
    CheckmarkCircle02Icon,
    CpuIcon,
    Globe02Icon,
    Mic02Icon,
    SpeechToTextIcon,
    StopWatchIcon,
    TextIcon,
    Time02Icon,
} from "@hugeicons/core-free-icons";
import type { CSSProperties } from "react";
import { useMemo } from "react";
// amber banner shown when the OS has not granted the
// keyboard-monitoring (Accessibility / input-group) permission. Mirrors
// the MicrophonePermissionBanner placement on the Microphone page.
import { KeyboardPermissionBanner } from "@/components/KeyboardPermissionBanner";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import {
    canShareStats,
    computeShareStats,
    useStatsShare,
} from "@/hooks/useStatsShare";
import { getLocale, t } from "@/i18n/i18n";
import { compactNumber, formatDuration } from "@/lib/format";
import { useThemePalette } from "@/lib/theme-palette";
import {
    formatDevice,
    formatLanguage,
    formatModel,
} from "@/lib/utils/configDisplay";
import { DashboardSkeleton } from "./dashboard/components/DashboardSkeleton";
import { ActivityChart } from "./dashboard/components/SevenDayActivityChart";
import { TimeRangeSelector } from "./dashboard/components/TimeRangeSelector";
import { useDashboardData } from "./dashboard/hooks/useDashboardData";

// Hidden share-image capture target container style.
//
// Hoisted to a module-level constant so the object identity is stable
// across renders — a fresh inline `style={{...}}` literal on every
// render breaks `React.memo` on the share-image subtree (each render
// produces a new object reference, forcing a re-render even when the
// underlying stats haven't changed). The container is position:absolute
// + zIndex:-100 + pointerEvents:none so it's painted off-screen for
// html-to-image capture but never visible or interactive to the user.
// The values are static (no render-time computation), so a single
// module-level instance is correct for all Dashboard renders.
const SHARE_IMAGE_CAPTURE_STYLE: CSSProperties = {
	position: "absolute",
	top: 0,
	left: 0,
	zIndex: -100,
	pointerEvents: "none",
};

/**
 * Trend vs the previous period of the same length.
 *
 * Returns null when there's no prior period to compare (All Time) or
 * the previous period had no activity (division by zero). A zero delta
 * yields a flat trend (pct 0).
 */
function computeTrend(
	cur: number,
	prev: number | null | undefined,
): StatTrend | null {
	if (prev === null || prev === undefined || prev <= 0) return null;
	const delta = cur - prev;
	if (delta === 0) return { pct: 0, up: true };
	return { pct: Math.abs(Math.round((delta / prev) * 100)), up: delta > 0 };
}

//DashboardPage obtains `navigate` via useNavigation directly.
export default function DashboardPage() {
	const { navigate } = useNavigation();
	const { call } = usePython();
	const {
		data,
		configRaw,
		configDir,
		refreshing,
		handleManualRefresh,
		agoLabel,
		fetchError,
		range,
		setRange,
		period,
		activity,
		correctionStats,
	} = useDashboardData({ call });
	const {
		imageRef,
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	} = useStatsShare();
	// Live theme palette for the share image — re-reads when the theme
	// changes so the exported PNG always matches the active preset.
	const themePalette = useThemePalette();

	// Memoise the ShareStats object so its identity is stable
	// across unrelated re-renders (e.g. refreshing flag toggles,
	// agoLabel changes). Without this, every Dashboard re-render
	// produced a fresh `computeShareStats(...)` return value,
	// defeating the React.memo wrapper on StatsShareImage.
	// Declared BEFORE the `if (!data)` early return so the hook
	// order is stable across renders (rules-of-hooks).
	const shareStats = useMemo(
		() =>
			data && configRaw
				? computeShareStats(
						{
							count: data.todayCount,
							chars: data.todayChars,
							word_count: data.todayWordCount,
							duration: data.todayDuration,
						},
						configRaw.asr_backend,
						{
							totalCount: data.totalCount,
							totalChars: data.totalChars,
							totalDuration: data.totalDuration,
							activeDays: data.activeDays,
							currentStreak: data.currentStreak,
							// Only include the setup line when a model is genuinely
							// installed (data.model/device are null otherwise) —
							// the share image never claims a model that isn't
							// there, and the values are pre-formatted for
							// display ("Tiny", "GPU").
							model: data.model ? formatModel(data.model) : "",
							device: data.device ? formatDevice(data.device) : "",
						},
					)
				: null,
		[data, configRaw],
	);

	// Skeleton shown only on FIRST load (when `!data`); subsequent
	// refreshes keep prior data visible (refreshing flag drives the
	// LastUpdatedIndicator spinner instead).
	// When `fetchError` is set and `data` is null, the first fetch failed —
	// render an error state with a Retry button instead of the skeleton.
	if (!data) {
		if (fetchError) {
			return (
				<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col items-center justify-center px-16 pt-28 pb-6">
					<EmptyState
						variant="error"
						icon={AlertCircleIcon}
						title={t("analytics.refreshFailed")}
						description={t("analytics.refreshFailedHint")}
						actionLabel={t("analytics.retry")}
						onAction={handleManualRefresh}
					/>
				</div>
			);
		}
		return <DashboardSkeleton />;
	}

	const d = data;
	const isFirstRun = d.totalCount === 0; // Empty-state CTA
	const shareActions = {
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	};

	return (
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
			<PageHeading
				title={t("analytics.title")}
				description={t("analytics.description")}
			>
				{" "}
				{data &&
					configRaw &&
					canShareStats({
						todayCount: data.todayCount,
						totalCount: data.totalCount,
					}) && (
						<ShareStatsDialog
							actions={shareActions}
							stats={shareStats}
							palette={themePalette}
						/>
					)}
			</PageHeading>

			{/* amber keyboard-permission banner — placed
				immediately under PageHeading so the user sees the "click to
				fix" prompt before scrolling into the analytics cards. Renders
				null when permission is granted / not needed, so the layout is
				unchanged on platforms where the banner doesn't apply. */}
			<KeyboardPermissionBanner />

			<div className="flex flex-wrap items-center justify-between gap-3 pb-2">
				<TimeRangeSelector value={range} onChange={setRange} />
				<LastUpdatedIndicator
					agoLabel={agoLabel}
					onRefresh={handleManualRefresh}
					refreshing={refreshing}
				/>
			</div>

			{isFirstRun ? (
				<EmptyState
					icon={Mic02Icon}
					title={t("analytics.noDataTitle")}
					description={t("analytics.noDataDescription", {
						hotkey: configRaw?.hotkey || "F2",
					})}
					actionLabel={t("analytics.startDictation")}
					actionIcon={SpeechToTextIcon}
					onAction={() => navigate("home")}
				/>
			) : (
				<div className="space-y-6">
					<div className="grid grid-cols-2 gap-3 md:grid-cols-4">
						{/* Single dictations card — DATA-CONSISTENCY fix: the
						    old "Dictations" card (window count from the
						    500-row history sample) and the range-blind
						    "Total Dictations" card (true all-time row count
						    from get_history_count) are merged into ONE card
						    whose VALUE respects the selected range. The two
						    previously disagreed under "All Time" (500 vs
						    893): period.count caps at the sample size while
						    totalCount is the true DB row count. For bounded
						    ranges the window count is exact (recent rows are
						    always inside the DESC-ordered sample); for All
						    Time the true count is used so the card is never
						    sample-capped. */}
						<DashboardStatCard
							label={t("analytics.totalDictationsPeriod", {
								range: t(`analytics.range.${range}`),
							})}
							value={
								range === "all" ? String(d.totalCount) : String(period.count)
							}
							icon={SpeechToTextIcon}
							trend={computeTrend(period.count, period.prev?.count)}
						/>
						<DashboardStatCard
							label={t("analytics.recordingTime")}
							value={formatDuration(period.duration)}
							icon={Time02Icon}
							trend={computeTrend(period.duration, period.prev?.duration)}
						/>
						<DashboardStatCard
							label={t("analytics.activeDays")}
							value={String(period.activeDays)}
							icon={Calendar01Icon}
							sublabel={
								d.currentStreak > 0
									? t("analytics.dayStreak", {
											count: String(d.currentStreak),
										})
									: undefined
							}
						/>
						{/* Characters — reuses the Home page Characters card's
						    formatter (formatCompactNumber from StatCards) so
						    the K-abbreviation + rounding config carries over
						    unchanged; wired to the same range-filtered char
						    count as the rest of the page. */}
						<DashboardStatCard
							label={t("analytics.cards.chars")}
							value={formatCompactNumber(period.chars)}
							icon={TextIcon}
						/>
						{/* Corrections moved to the derived-metrics card row below
							(so the top row divides evenly into 4 cards). */}
					</div>

					{/* NOTE: no "no dictations in this period" caption here —
						the empty chart already communicates "no data". */}

					<ActivityChart range={range} activity={activity} />

					{/* Derived metrics — card-styled row directly below the
						chart (Avg chars / Longest session / Corrections). */}
					<div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
						<QuickInfoCard
							icon={TextIcon}
							label={t("analytics.avgCharsLabel")}
							value={period.avgCharsPerDictation.toLocaleString(getLocale())}
						/>
						<QuickInfoCard
							// Stopwatch (not a clock) — the top-row Recording
							// Time card already uses Time02Icon; a stopwatch
							// reads as "longest single session" at a glance.
							icon={StopWatchIcon}
							label={t("analytics.longestLabel")}
							value={formatDuration(period.longestSession)}
						/>
						<QuickInfoCard
							icon={CheckmarkCircle02Icon}
							label={t("analytics.corrections")}
							value={compactNumber(correctionStats.corrections)}
							sublabel={
								correctionStats.rate !== null
									? t("analytics.correctionsRate", {
											pct: String(Math.round(correctionStats.rate * 100)),
											dictations: String(correctionStats.dictations),
										})
									: undefined
							}
						/>
					</div>

					{/* Current Setup — system/config info, demoted below the
						usage analytics so it doesn't compete for attention. */}
					<section
						className="space-y-2.5"
						aria-label={t("analytics.currentSetup")}
					>
						<h3 className="text-[11px] font-semibold uppercase tracking-wider text-(--text-muted)">
							{t("analytics.currentSetup")}
						</h3>
						<div className="grid grid-cols-1 gap-3 md:grid-cols-3">
							<QuickInfoCard
								muted
								icon={AiBrain03Icon}
								label={t("analytics.model")}
								value={
									d.model ? formatModel(d.model) : t("analytics.notSelected")
								}
							/>
							<QuickInfoCard
								muted
								// Chip/processor icon — reads as "compute device".
								icon={CpuIcon}
								label={t("analytics.device")}
								value={
									d.device ? formatDevice(d.device) : t("analytics.notSelected")
								}
							/>
							<QuickInfoCard
								muted
								// Classic globe (meridian + latitude lines) — the
								// previous circle-with-contours icon read as an
								// indistinct blob at 20px.
								icon={Globe02Icon}
								label={t("analytics.language")}
								value={formatLanguage(d.language)}
							/>
						</div>
					</section>

					<p className="pb-4 text-center text-xs text-(--text-muted)">
						{t("analytics.dataPath", {
							path: configDir || "~/.voice-typer/",
						})}
					</p>
				</div>
			)}

			{/* Hidden share-image capture target (no clipPath — EXPORT-FIX). */}
			<div ref={imageRef} aria-hidden style={SHARE_IMAGE_CAPTURE_STYLE}>
				{shareStats && (
					<StatsShareImage stats={shareStats} palette={themePalette} />
				)}
			</div>
		</div>
	);
}
