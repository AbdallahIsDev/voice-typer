// History page loading skeleton (list area only).
//
// History renders its heading + toolbar immediately (they are static)
// and swaps ONLY the activity list while records load, so this
// skeleton replaces just the list slot (`pages/History.tsx` inline
// branch). It mirrors `components/dashboard/ActivityList.tsx` exactly:
// per-day `rounded-lg border border-border/5 bg-(--bg-subtle)` section
// cards with a `px-4 pt-3 pb-1` day header, then `divide-y` rows of
// `flex items-center gap-3 px-4 py-2`: each row a clamped text block
// (text-sm lines → h-5, meta line text-xs → h-4) and a trailing
// 3-button ghost action column (size-6).

import {
	IconButtonSkeleton,
	SkeletonRegion,
} from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";

const CARD_IDS = ["history-day-0", "history-day-1"];

function HistoryRow() {
	return (
		<div className="flex items-center gap-3 px-4 py-2">
			<div className="flex min-w-0 flex-1 flex-col gap-1">
				<Skeleton className="h-5 w-11/12" />
				<Skeleton className="h-5 w-3/5" />
				<Skeleton className="h-4 w-28" />
			</div>
			<div className="flex items-center gap-1">
				<IconButtonSkeleton />
				<IconButtonSkeleton />
				<IconButtonSkeleton />
			</div>
		</div>
	);
}

export function HistorySkeleton() {
	return (
		<SkeletonRegion
			label={t("a11y.loading")}
			className="flex w-full flex-col gap-4"
		>
			{CARD_IDS.map((cardId) => (
				<section
					key={cardId}
					className="w-full rounded-lg border border-border/5 bg-(--bg-subtle)"
				>
					<div className="px-4 pt-3 pb-1">
						<Skeleton className="h-4 w-24" />
					</div>
					<div className="divide-y divide-border/5">
						<HistoryRow />
						<HistoryRow />
						<HistoryRow />
					</div>
				</section>
			))}
		</SkeletonRegion>
	);
}
