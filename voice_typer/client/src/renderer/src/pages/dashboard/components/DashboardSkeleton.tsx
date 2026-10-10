// Dashboard loading skeleton.
// Mirrors the loaded Dashboard layout (`pages/Dashboard.tsx`): shell
// (gap-6) → PageHeading with an action row (share + refresh icon
// buttons) → the merged stat card (ONE bordered surface holding SIX
// `min-h-24` p-3 cells in two divided rows of three, icon+label row on
// top, value pinned bottom via mt-auto) → the activity chart card
// (one-line title/range header, h-36 plot with y-axis + 7 bars +
// x-label row).
// The range selector is NOT mirrored here: it lives in the app title
// bar, which renders immediately and is outside this page's tree. The
// heatmap card below the activity chart is not mirrored either — it has
// never been part of this skeleton.
// The root stays a `<section aria-busy>` (NOT a live region): the
// live-region guard test (`data-pages-live-region-guards.test.tsx`)
// pins the first-paint skeleton at ZERO live regions, the hydrated
// page owns the announcements.

import { HeadingSkeleton } from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";

const ROW_1_IDS = ["dash-stat-0", "dash-stat-1", "dash-stat-2"];
const ROW_2_IDS = ["dash-stat-3", "dash-stat-4", "dash-stat-5"];
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

function StatCardSkeleton() {
	// Chrome-less: the cell sits inside the merged group below, which
	// owns the radius/border/background, so only the padding lives here.
	return (
		<div className="flex min-h-24 flex-col gap-2 p-3">
			<div className="flex min-w-0 items-center gap-2">
				<Skeleton className="h-5 w-5 shrink-0" />
				<Skeleton className="h-4 w-16" />
			</div>
			<div className="mt-auto flex items-end justify-between gap-2">
				<Skeleton className="h-8 w-16" />
				<Skeleton className="h-3 w-8 shrink-0" />
			</div>
		</div>
	);
}

// Body below the heading (stat card + chart), no shell. The first-load
// path in Dashboard.tsx renders the REAL heading (Share always mounted,
// C-CACHE-6) above this body instead of the heading skeleton.
export function DashboardSkeletonBody() {
	return (
		<>
			<div className="overflow-hidden rounded-lg border border-border/8 bg-surface-subtle">
				<div className="grid grid-cols-1 divide-y divide-border/8 md:grid-cols-3 md:divide-x md:divide-y-0">
					{ROW_1_IDS.map((id) => (
						<StatCardSkeleton key={id} />
					))}
				</div>
				<div className="grid grid-cols-1 divide-y divide-border/8 border-t border-border/8 md:grid-cols-3 md:divide-x md:divide-y-0">
					{ROW_2_IDS.map((id) => (
						<StatCardSkeleton key={id} />
					))}
				</div>
			</div>
			<div className="flex flex-col gap-4 rounded-lg border border-border/8 bg-surface-subtle p-4">
				{/* Header mirrors the activity card's: title and range/unit on
			    ONE baseline, no icon chip — the chip this block used to
			    reserve was removed from the card, and a skeleton taller
			    than the thing it stands in for is a layout shift on
			    hydration. */}
				<div className="flex items-baseline justify-between gap-3">
					<Skeleton className="h-5 w-20" />
					<Skeleton className="h-4 w-24 shrink-0" />
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
				{/* Same fixed two-line row as the loaded chart (C-LIFE-2):
			    one placeholder line anchored top, so skeleton and
			    content boxes match on every range. */}
				<div className="flex h-8 gap-2">
					{CHART_LABEL_IDS.map((id) => (
						<Skeleton key={id} className="mx-auto h-3 w-8" />
					))}
				</div>
			</div>
			{/* Heatmap reservation: the loaded card below the chart renders
		    a fluid grid whose height is width-driven (aspect ~6/1,
		    C-LIFE-2). Without this block the first load grows the page
		    when the card arrives. Same geometry as the real card, no
		    fake cells — a plain reserved box. */}
			<div className="flex flex-col gap-4 rounded-lg border border-border/8 bg-surface-subtle p-4">
				<div className="flex items-baseline justify-between gap-3">
					<Skeleton className="h-5 w-32" />
					<Skeleton className="h-4 w-20 shrink-0" />
				</div>
				<Skeleton className="aspect-[6/1] min-h-16 w-full" />
				<div className="flex items-center justify-between gap-3">
					<Skeleton className="h-3 w-24" />
					<Skeleton className="h-3 w-16 shrink-0" />
				</div>
			</div>
		</>
	);
}

export function DashboardSkeleton() {
	return (
		<section
			aria-label={t("analytics.loadingAria")}
			aria-busy="true"
			className="mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6"
		>
			{/* The heading's action row: the share trigger and the refresh
			    button, both `size="icon"` (36px) squares. Reserving them
			    here keeps the stat card from jumping up when the heading
			    hydrates with its actions. */}
			<HeadingSkeleton
				action={
					<>
						<Skeleton className="h-9 w-9 rounded-lg" />
						<Skeleton className="h-9 w-9 rounded-lg" />
					</>
				}
			/>
			<DashboardSkeletonBody />
		</section>
	);
}
