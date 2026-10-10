//thin composition root. Data-fetch / refresh / event-subscription
// lives in `./dashboard/hooks/useDashboardData`; pure helpers in
// `./dashboard/lib/{streaks,format,trend}`; presentational sub-components in
// `./dashboard/components/`. Only page-local composition stays here.
// Analytics layout:
//   1. Range selector (Today / 7 Days / 30 Days / All Time) lives in the
//      TITLE BAR, not here: it drives the stat cards AND the chart
//      together (single source: one history sample, UTC-correct day
//      bucketing, see the hook), and keeping it in the bar means it stays
//      reachable while the page scrolls. The page holds no range state of
//      its own, see `stores/useAnalyticsRange`.
//   2. Six range-aware stat cards in ONE divided card (two rows of
//      three, no gaps between cells): Dictations / Recording Time /
//      Words on top, Average Speed / Longest session / Corrections
//      below. All six carry the SAME trend indicator, each comparing
//      against the previous period of the same length, so no cell
//      reads as a different kind of thing. A cell whose baseline is
//      unusable (no previous window under "All Time", or a zero
//      previous value) omits the indicator rather than inventing a
//      comparison — that is `computeTrend`'s null contract, not a
//      per-card decision.
//   3. The activity chart (hourly for Today, daily otherwise) with a
//      y-axis, gridlines, and zero-vs-no-data distinction.
//   4. The dictation heatmap — deliberately range-INDEPENDENT: it always
//      spans the whole history the sample covers (capped at a year),
//      because a per-day contribution grid only reads at a scale of
//      months. See `./dashboard/components/ActivityHeatmap`. It also
//      carries the active-days + streak figures (they measure the same
//      long window, so a range-aware card in row 2 would contradict it).

import {
	AlertCircleIcon,
	CheckmarkCircle02Icon,
	Mic02Icon,
	SpeechToTextIcon,
	StopWatchIcon,
	TextIcon,
	Time02Icon,
} from "@hugeicons/core-free-icons";
import type { CSSProperties } from "react";
import { useMemo } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { ShareStatsDialog } from "@/components/dashboard/ShareStatsDialog";
import { StatCard } from "@/components/dashboard/StatCard";
import { formatCompactNumber } from "@/components/dashboard/StatCards";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { EmptyState } from "@/components/feedback/EmptyState";
import { HotkeyChips } from "@/components/hotkey/HotkeyChips";
import { formatHotkey } from "@/components/hotkey/hotkey-utils";
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
import { t } from "@/i18n/i18n";
import { compactNumber, formatDuration } from "@/lib/format";
import { useThemePalette } from "@/lib/theme-palette";
import { formatDevice, formatModel } from "@/lib/utils/configDisplay";
import { computeTrend } from "@/pages/dashboard/lib/trend";
import { ActivityHeatmap } from "./dashboard/components/ActivityHeatmap";
import {
	DashboardSkeleton,
	DashboardSkeletonBody,
} from "./dashboard/components/DashboardSkeleton";
import { ActivityChart } from "./dashboard/components/SevenDayActivityChart";
import { useDashboardData } from "./dashboard/hooks/useDashboardData";

// Hidden share-image capture target container style.
// Hoisted to a module-level constant so the object identity is stable
// across renders, a fresh inline `style={{...}}` literal on every
// render breaks `React.memo` on the share-image subtree (each render
// produces a new object reference, forcing a re-render even when the
// underlying stats haven't changed). The container is position:absolute
// + zIndex:-100 + pointerEvents:none so it's painted off-screen for
// html-to-image capture but never visible or interactive to the user.
// The values are static (no render-time computation), so a single
// module-level instance is correct for all Dashboard renders.
// Placeholder the translated no-data sentence is split on so the shortcut
// renders as `HotkeyChips` (C-UI-1) instead of the raw config syntax
// (`<caps_lock>`). A private-use codepoint can't collide with translated text.
const NO_DATA_HOTKEY_MARKER = "\uFFF0";

function renderNoDataDescription(template: string, hotkey: string) {
	const [before, after] = template.split(NO_DATA_HOTKEY_MARKER);
	if (after === undefined) {
		// Translation dropped the placeholder, show it verbatim rather than
		// silently swallowing a chunk of the sentence.
		return template;
	}
	return (
		<>
			{before}
			<HotkeyChips keys={hotkey} />
			{after}
		</>
	);
}

const SHARE_IMAGE_CAPTURE_STYLE: CSSProperties = {
	position: "absolute",
	top: 0,
	left: 0,
	zIndex: -100,
	pointerEvents: "none",
};

//DashboardPage obtains `navigate` via useNavigation directly.
export default function DashboardPage() {
	const { navigate } = useNavigation();
	const { call } = usePython();
	const {
		data,
		configRaw,
		refreshing,
		handleManualRefresh,
		agoLabel,
		fetchError,
		range,
		period,
		activity,
		heatmap,
		correctionStats,
		customReady,
		customCapped,
		customWindowLabel,
	} = useDashboardData({ call });
	const {
		imageRef,
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	} = useStatsShare();
	// Live theme palette for the share image, re-reads when the theme
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
							totalWords: data.totalWords,
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

	// Plain (non-memoised) action map: stable function identities from
	// the hook, declared before the early return so the loading path
	// can mount the Share trigger too (C-CACHE-6).
	const shareActions = {
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	};

	// Share availability: the trigger is ALWAYS mounted (C-CACHE-6),
	// disabled until real data can back it. Touches nothing when data
	// is absent, so computing it before the early return is safe.
	const shareDisabled =
		!data ||
		!configRaw ||
		!canShareStats({
			todayCount: data?.todayCount ?? 0,
			totalCount: data?.totalCount ?? 0,
		});

	// Skeleton shown only on FIRST load (when `!data`); revisits seed
	// from the snapshot cache and skip it. A custom window with no
	// rows yet skeletonizes too (its fetch rides outside `data`).
	// When `fetchError` is set and `data` is null, the first fetch failed —
	// render an error state with a Retry button instead of the skeleton.
	// A custom window with no synced rows lands here too: loading shows
	// the skeleton, a failed fetch shows the error (zeros would read as
	// "no activity" instead of "load failed").
	if (!data || (range === "custom" && !customReady)) {
		if (fetchError) {
			return (
				<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col items-center justify-center gap-6 px-16 pt-20 pb-6">
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
		// First load renders the REAL heading (Share mounted + disabled)
		// above the skeleton body: actions never vanish mid-load, and the
		// heading keeps zero live regions (no LastUpdatedIndicator here).
		if (!data) {
			return (
				<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6">
					<PageHeading
						title={t("analytics.title")}
						description={t("analytics.description")}
					>
						<ShareStatsDialog
							actions={shareActions}
							stats={shareStats}
							palette={themePalette}
							disabled
						/>
					</PageHeading>
					<DashboardSkeletonBody />
				</div>
			);
		}
		return <DashboardSkeleton />;
	}

	const d = data;
	const isFirstRun = d.totalCount === 0; // Empty-state CTA
	// Average speaking speed for the selected window (words per minute
	// of recorded audio). Null when the window has no recorded audio:
	// the card then shows "—" and carries no trend instead of
	// presenting a number the data cannot support.
	const wpm =
		period.duration > 0
			? Math.round(period.wordCount / (period.duration / 60))
			: null;
	const prevWpm =
		period.prev !== null && period.prev.duration > 0
			? Math.round(period.prev.wordCount / (period.prev.duration / 60))
			: null;

	return (
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6">
			<PageHeading
				title={t("analytics.title")}
				description={t("analytics.description")}
			>
				<LastUpdatedIndicator
					agoLabel={agoLabel}
					onRefresh={handleManualRefresh}
					refreshing={refreshing}
				/>
				<ShareStatsDialog
					actions={shareActions}
					stats={shareStats}
					palette={themePalette}
					disabled={shareDisabled}
				/>
			</PageHeading>

			{/* amber keyboard-permission banner, placed
				immediately under PageHeading so the user sees the "click to
				fix" prompt before scrolling into the analytics cards. Renders
				null when permission is granted / not needed, so the layout is
				unchanged on platforms where the banner doesn't apply. */}
			<KeyboardPermissionBanner />

			{isFirstRun ? (
				<EmptyState
					icon={Mic02Icon}
					title={t("analytics.noDataTitle")}
					description={renderNoDataDescription(
						t("analytics.noDataDescription", { hotkey: NO_DATA_HOTKEY_MARKER }),
						formatHotkey(configRaw?.hotkey || "F2"),
					)}
					actionLabel={t("analytics.startDictation")}
					actionIcon={SpeechToTextIcon}
					onAction={() => navigate("home")}
				/>
			) : (
				<div className="flex flex-col gap-4">
					{/* ONE merged card, not six separate ones: the shared
					    container owns the radius/border/background and 1px
					    rules separate the cells, so there is no gap between
					    them (C-DESIGN-2). Each row is its own divided grid,
					    stacked cells on narrow windows and three columns
					    from md up, with the row rule (border-t) spanning
					    both layouts. */}
					<div className="overflow-hidden rounded-lg border border-border/8 bg-surface-subtle">
						<div className="grid grid-cols-1 divide-y divide-border/8 md:grid-cols-3 md:divide-x md:divide-y-0">
							{/* Single dictations card, DATA-CONSISTENCY fix: the
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
							<StatCard
								inGroup
								// Plain label (no range suffix), the title bar's
								// range control + the chart's subtitle already
								// state the active window; the suffix made this
								// the only truncating label in the row
								// ("Total Dictations (7 D…").
								label={t("analytics.totalDictations")}
								value={
									range === "all" ? String(d.totalCount) : String(period.count)
								}
								icon={SpeechToTextIcon}
								trend={computeTrend(period.count, period.prev?.count)}
							/>
							<StatCard
								inGroup
								label={t("analytics.recordingTime")}
								value={formatDuration(period.duration)}
								icon={Time02Icon}
								trend={computeTrend(period.duration, period.prev?.duration)}
							/>
							{/* Words, reuses the Home StatCards compact formatter
							    (formatCompactNumber) so the K-abbreviation +
							    rounding config carries over unchanged. The value
							    is the exact word count summed over the window: the
							    renderer never re-counts text (list previews are
							    truncated) and chars/5 would only approximate it. */}
							<StatCard
								inGroup
								label={t("analytics.wordsLabel")}
								value={formatCompactNumber(period.wordCount)}
								icon={TextIcon}
								trend={computeTrend(period.wordCount, period.prev?.wordCount)}
							/>
							{/* Active Days is deliberately NOT a cell here: it
							    measures the heatmap's whole-year window, not the
							    selected range, so it lives in the heatmap header
							    below. */}
						</div>
						<div className="grid grid-cols-1 divide-y divide-border/8 border-t border-border/8 md:grid-cols-3 md:divide-x md:divide-y-0">
							{/* Average speed: words ÷ recorded minutes; "—" and no
							    trend when the window has no recorded audio, so an
							    empty window never claims a speed. */}
							<StatCard
								inGroup
								label={t("analytics.avgSpeedLabel")}
								value={wpm !== null ? `${wpm} WPM` : "—"}
								icon={Mic02Icon}
								trend={wpm !== null ? computeTrend(wpm, prevWpm) : null}
							/>
							<StatCard
								inGroup
								// Stopwatch (not a clock), the top-row Recording Time
								// card already uses Time02Icon; a stopwatch reads as
								// "longest single session" at a glance.
								icon={StopWatchIcon}
								label={t("analytics.longestLabel")}
								value={formatDuration(period.longestSession)}
								// Compares against the previous window's longest
								// single session — the like-for-like baseline.
								// Absent under "All Time" (no prior period).
								trend={computeTrend(
									period.longestSession,
									period.prev?.longestSession,
								)}
							/>
							<StatCard
								inGroup
								label={t("analytics.corrections")}
								value={compactNumber(correctionStats.corrections)}
								icon={CheckmarkCircle02Icon}
								// Vocabulary corrections APPLIED inside the window.
								// More is the feature working, so the shared
								// up = green convention holds here unchanged.
								trend={computeTrend(
									correctionStats.corrections,
									correctionStats.prevCorrections,
								)}
							/>
						</div>
					</div>

					{/* NOTE: no "no dictations in this period" caption here —
						the empty chart already communicates "no data". */}

					<ActivityChart
						range={range}
						activity={activity}
						customWindowLabel={customWindowLabel}
					/>
					{/* Custom windows that hit the page cap serve sampled
				    stats: say so under the chart, next to the numbers
				    it qualifies. The animator (C-ANIM-1) smooths the
				    line's arrival once the window fetch lands — the
				    wrapper stays mounted for the whole custom range so
				    the toggle (not a remount) is what animates. */}
					{range === "custom" && (
						<div className="collapse-root" data-open={customCapped}>
							<div>
								<p className="text-center text-xs text-muted-foreground">
									{t("analytics.customRangeCapped", {
										count: String(period.count),
									})}
								</p>
							</div>
						</div>
					)}

					{/* Long-window consistency view. Placed AFTER the
						range-aware block (cards → chart → heatmap) so
						the selected range's analysis stays contiguous. The
						card states its own covered range in its subtitle
						because it ignores the range control in the title bar,
						and it owns the active-days + streak figures for the same
						reason: both count the whole history, not the selected
						window, so they must not sit next to range-aware
						numbers. */}
					<ActivityHeatmap heatmap={heatmap} currentStreak={d.currentStreak} />
				</div>
			)}

			{/* Hidden share-image capture target (no clipPath, EXPORT-FIX). */}
			<div ref={imageRef} aria-hidden style={SHARE_IMAGE_CAPTURE_STYLE}>
				{shareStats && (
					<StatsShareImage stats={shareStats} palette={themePalette} />
				)}
			</div>
		</div>
	);
}
