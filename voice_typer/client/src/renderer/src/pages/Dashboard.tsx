//thin composition root. Data-fetch / refresh / event-subscription
// lives in `./dashboard/hooks/useDashboardData`; pure helpers in
// `./dashboard/lib/{streaks,format}`; presentational sub-components in
// `./dashboard/components/`. LOC history: 732 (pre-split) → <150 (post-split).

import {
	Activity03Icon,
	AiBrain03Icon,
	AlertCircleIcon,
	Calendar01Icon,
	File02Icon,
	LayoutGridIcon,
	Mic02Icon,
	Share08Icon,
	SpeechToTextIcon,
	Time02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { CSSProperties } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { DashboardStatCard } from "@/components/dashboard/DashboardStatCard";
import { QuickInfoCard } from "@/components/dashboard/QuickInfoCard";
import { StatsShareImage } from "@/components/dashboard/StatsShareImage";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Button } from "@/components/ui/button.tsx";
import { useNavigation } from "@/hooks/useNavigation";
import { usePython } from "@/hooks/usePython";
import {
	canShareStats,
	computeShareStats,
	useStatsShare,
} from "@/hooks/useStatsShare";
import { getLocale, t } from "@/i18n/i18n";
import { compactNumber, formatDuration } from "@/lib/format";
import { DashboardSkeleton } from "./dashboard/components/DashboardSkeleton";
import { SevenDayActivityChart } from "./dashboard/components/SevenDayActivityChart";
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
	} = useDashboardData({ call });
	const { imageRef, shareAsImage } = useStatsShare();

	// Fix #19: skeleton shown only on FIRST load (when `!data`); subsequent
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
	const isFirstRun = d.totalCount === 0; // Fix #10: empty-state CTA
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

			<div className="flex justify-end pb-2">
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
					description={t("analytics.noDataDescription")}
					actionLabel={t("analytics.startDictation")}
					actionIcon={SpeechToTextIcon}
					onAction={() => navigate("home")}
				/>
			) : (
				<div className="space-y-8">
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

					<SevenDayActivityChart data={d} />

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

					<p className="text-xs text-(--text-muted) text-center pb-4">
						{t("analytics.dataPath")}
					</p>
				</div>
			)}

			{/* Hidden share-image capture target (no clipPath — EXPORT-FIX). */}
			<div ref={imageRef} aria-hidden style={SHARE_IMAGE_CAPTURE_STYLE}>
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
