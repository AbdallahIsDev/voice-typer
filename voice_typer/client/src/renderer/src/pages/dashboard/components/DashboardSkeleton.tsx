// Dashboard loading skeleton.
//
// Mirrors the loaded Dashboard layout block-for-block
// (`pages/Dashboard.tsx`): shell (gap-6) → PageHeading → range row
// (TimeRangeSelector pill group LEFT + LastUpdatedIndicator right) →
// 4 StatCards (`grid-cols-2 md:grid-cols-4`, min-h-24, p-3, icon+label
// row on top, value pinned bottom via mt-auto) → the activity chart
// card (header with h-8 icon chip, h-36 plot with y-axis + 7 bars +
// x-label row) → the derived-metrics QuickInfo row (`sm:grid-cols-3`)
// → the "Current Setup" heading + muted 3-col QuickInfo grid.
//
// The root stays a `<section aria-busy>` (NOT a live region): the
// live-region guard test (`data-pages-live-region-guards.test.tsx`)
// pins the first-paint skeleton at ZERO live regions, the hydrated
// page owns the announcements.

import { HeadingSkeleton } from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";

const STAT_IDS = ["dash-stat-0", "dash-stat-1", "dash-stat-2", "dash-stat-3"];
const BAR_HEIGHTS = ["h-16", "h-28", "h-20", "h-24", "h-32", "h-12", "h-20"];
const CHART_LABEL_IDS = [
	"dash-x-0",
	"dash-x-1",
	"dash-x-2",
	"dash-x-3",
	"dash-x-4",
	"dash-x-5",
	"dash-x-6",
];
const DERIVED_IDS = ["dash-derived-0", "dash-derived-1", "dash-derived-2"];
const SETUP_IDS = ["dash-setup-0", "dash-setup-1", "dash-setup-2"];

function StatCardSkeleton() {
	return (
		<div className="flex min-h-24 flex-col gap-2 rounded-xl border border-border/5 bg-(--bg-subtle) p-3">
			<div className="flex min-w-0 items-center gap-2">
				<Skeleton className="h-5 w-5 shrink-0" />
				<Skeleton className="h-4 w-16" />
			</div>
			<Skeleton className="mt-auto h-8 w-16" />
			<Skeleton className="h-3 w-12" />
		</div>
	);
}

function QuickInfoCardSkeleton({ compact = false }: { compact?: boolean }) {
	return (
		<div
			className={`flex items-stretch gap-3 rounded-xl border border-border/5 bg-(--bg-subtle) ${compact ? "p-3" : "p-4"}`}
		>
			<Skeleton className="h-5 w-5 shrink-0" />
			<div className="flex min-w-0 flex-1 flex-col gap-2">
				<Skeleton className="h-3 w-14" />
				<Skeleton className="mt-auto h-5 w-20" />
			</div>
		</div>
	);
}

export function DashboardSkeleton() {
	return (
		<section
			aria-label={t("analytics.loadingAria")}
			aria-busy="true"
			className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6"
		>
			<HeadingSkeleton />
			<div className="flex flex-wrap items-center justify-between gap-3 pb-2">
				<div className="flex gap-1 rounded-full border border-border/5 bg-(--bg-subtle) p-1">
					{STAT_IDS.map((id) => (
						<Skeleton key={id} className="h-7 w-16 rounded-full" />
					))}
				</div>
				<Skeleton className="h-4 w-28" />
			</div>
			<div className="grid grid-cols-2 gap-3 md:grid-cols-4">
				{STAT_IDS.map((id) => (
					<StatCardSkeleton key={id} />
				))}
			</div>
			<div className="flex flex-col gap-4 rounded-xl border border-border/5 bg-(--bg-subtle) p-4">
				<div className="flex items-center gap-2.5">
					<Skeleton className="h-8 w-8 rounded-lg" />
					<div className="flex min-w-0 flex-col gap-1">
						<Skeleton className="h-5 w-32" />
						<Skeleton className="h-4 w-44" />
					</div>
				</div>
				<div className="flex items-end gap-3">
					<Skeleton className="h-36 w-7" />
					<div className="flex flex-1 items-end gap-2">
						{BAR_HEIGHTS.map((height, i) => (
							<Skeleton
								// biome-ignore lint/suspicious/noArrayIndexKey: static bar-height list, order never changes
								key={`dash-bar-${i}`}
								className={`w-full max-w-8 rounded-t-[4px] ${height}`}
							/>
						))}
					</div>
				</div>
				<div className="flex gap-2">
					{CHART_LABEL_IDS.map((id) => (
						<Skeleton key={id} className="mx-auto h-3 w-8" />
					))}
				</div>
			</div>
			<div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
				{DERIVED_IDS.map((id) => (
					<QuickInfoCardSkeleton key={id} />
				))}
			</div>
			<div className="flex flex-col gap-2.5">
				<Skeleton className="h-4 w-28" />
				<div className="grid grid-cols-1 gap-3 md:grid-cols-3">
					{SETUP_IDS.map((id) => (
						<QuickInfoCardSkeleton key={id} compact />
					))}
				</div>
			</div>
		</section>
	);
}
