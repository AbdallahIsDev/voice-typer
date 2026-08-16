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

import {
	Activity03Icon,
	AiBrain03Icon,
	AlertCircleIcon,
	Calendar01Icon,
	File02Icon,
	LayoutGridIcon,
	Mic02Icon,
	SpeechToTextIcon,
	Time02Icon,
} from "@hugeicons/core-free-icons";
import type { CSSProperties } from "react";
import { useMemo } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import {
	DashboardStatCard,
	type StatTrend,
} from "@/components/dashboard/DashboardStatCard";
import { QuickInfoCard } from "@/components/dashboard/QuickInfoCard";
import { ShareStatsMenu } from "@/components/dashboard/ShareStatsMenu";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { EmptyState } from "@/components/feedback/EmptyState";
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
import { DashboardSkeleton } from "./dashboard/components/DashboardSkeleton";
import { ActivityChart } from "./dashboard/components/SevenDayActivityChart";
import { TimeRangeSelector } from "./dashboard/components/TimeRangeSelector";
import { useDashboardData } from "./dashboard/hooks/useDashboardData";
import { weekdayLabel } from "./dashboard/lib/format";

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
							model: data.model,
							device: data.device,
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
				<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-center px-6 pt-28 pb-6">
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
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
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
					}) && <ShareStatsMenu actions={shareActions} />}
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
						<DashboardStatCard
							label={t("analytics.dictations")}
							value={String(period.count)}
							icon={SpeechToTextIcon}
							sublabel={t("analytics.charsValue", {
								count: period.chars.toLocaleString(getLocale()),
							})}
							trend={computeTrend(period.count, period.prev?.count)}
						/>
						<DashboardStatCard
							label={t("analytics.recordingTime")}
							value={formatDuration(period.duration)}
							icon={Time02Icon}
							sublabel={
								period.count > 0
									? t("analytics.avgPerDictation", {
											duration: formatDuration(
												Math.round(period.duration / period.count),
											),
										})
									: undefined
							}
							trend={computeTrend(period.duration, period.prev?.duration)}
						/>
						<DashboardStatCard
							label={t("analytics.totalDictations")}
							value={compactNumber(d.totalCount)}
							icon={File02Icon}
							tooltip={t("analytics.totalDictationsTooltip")}
							sublabel={t("analytics.charsValue", {
								count: d.totalChars.toLocaleString(getLocale()),
							})}
						/>
						<DashboardStatCard
							label={t("analytics.activeDays")}
							value={String(period.activeDays)}
							icon={Calendar01Icon}
							tooltip={t("analytics.activeDaysTooltip")}
							sublabel={
								d.currentStreak > 0
									? t("analytics.dayStreak", {
											count: String(d.currentStreak),
										})
									: t("analytics.noStreak")
							}
						/>
					</div>

					{/* "no activity in this range" note — the cards/chart are
						zero because the SELECTED period is empty, not because
						the app is broken (there IS history overall). */}
					{period.count === 0 && d.totalCount > 0 && (
						<p className="text-xs text-(--text-muted) text-center">
							{t("analytics.noActivityInRange")}
						</p>
					)}

					<ActivityChart
						range={range}
						activity={activity}
						totalCount={d.totalCount}
						sampleSize={d.sampleSize}
					/>

					{/* Derived metrics — only ones the data supports. */}
					<div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-1 text-xs text-(--text-muted)">
						<span>
							{t("analytics.avgCharsPerDictation", {
								count: period.avgCharsPerDictation.toLocaleString(getLocale()),
							})}
						</span>
						<span aria-hidden="true" className="text-border">
							·
						</span>
						<span>
							{t("analytics.longestSession", {
								duration: formatDuration(period.longestSession),
							})}
						</span>
						{period.peakWeekday !== null && (
							<>
								<span aria-hidden="true" className="text-border">
									·
								</span>
								<span>
									{t("analytics.peakWeekday", {
										day: weekdayLabel(period.peakWeekday),
									})}
								</span>
							</>
						)}
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
								value={d.model}
							/>
							<QuickInfoCard
								muted
								icon={LayoutGridIcon}
								label={t("analytics.device")}
								value={d.device.toUpperCase()}
							/>
							<QuickInfoCard
								muted
								icon={Activity03Icon}
								label={t("analytics.language")}
								value={d.language}
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
