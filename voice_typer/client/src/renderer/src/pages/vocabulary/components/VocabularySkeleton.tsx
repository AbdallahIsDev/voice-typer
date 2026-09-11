// Vocabulary page loading skeleton (full page).
//
// Mirrors the loaded Vocabulary layout (`pages/Vocabulary.tsx`): page
// shell → heading → CollectionToolbar (import/export/clear + sort
// pills left, Add pill right) → ONE `overflow-clip rounded-xl
// border-border/5 bg-(--bg-subtle)` list card containing the sticky
// column header grid (`auto 1fr auto` / `sm: auto 1fr 1fr 6.25rem`,
// px-3.5 py-2) and `divide-y` rows of the VocabListRow grid
// (`px-4 py-2`, checkbox + two text columns + trailing icon actions).

import {
	CheckboxSkeleton,
	HeadingSkeleton,
	IconButtonSkeleton,
	PageShell,
	PillSkeleton,
} from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

const ROW_IDS = [
	"vocab-row-0",
	"vocab-row-1",
	"vocab-row-2",
	"vocab-row-3",
	"vocab-row-4",
	"vocab-row-5",
	"vocab-row-6",
	"vocab-row-7",
];

function VocabularyRow() {
	return (
		<div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]">
			<CheckboxSkeleton className="self-start pt-0.5 sm:self-center sm:pt-0" />
			<Skeleton className="h-5 w-1/3" />
			<div className="col-start-2 flex min-w-0 items-center sm:col-start-auto">
				<Skeleton className="h-5 w-1/4" />
			</div>
			<div className="flex items-center justify-self-end gap-0.5">
				<IconButtonSkeleton />
				<IconButtonSkeleton />
				<IconButtonSkeleton />
			</div>
		</div>
	);
}

export function VocabularySkeleton() {
	return (
		<PageShell>
			<HeadingSkeleton />
			<div className="flex w-full flex-wrap items-center justify-between gap-2">
				<div className="flex flex-wrap items-center gap-2">
					<PillSkeleton className="w-24" />
					<PillSkeleton className="w-24" />
					<PillSkeleton className="w-24" />
					<PillSkeleton className="w-24" />
				</div>
				<PillSkeleton className="w-28" />
			</div>
			<div className="flex w-full flex-col gap-3">
				<div className="overflow-clip rounded-xl border border-border/5 bg-(--bg-subtle)">
					<div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 rounded-t-xl border-b border-border/5 bg-(--bg-subtle)/95 px-3.5 py-2 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]">
						<CheckboxSkeleton />
						<Skeleton className="h-4 w-14" />
						<Skeleton className="col-start-2 h-4 w-10 sm:col-start-auto" />
						<Skeleton className="h-4 w-12 justify-self-end" />
					</div>
					<div className="divide-y divide-border/5">
						{ROW_IDS.map((id) => (
							<VocabularyRow key={id} />
						))}
					</div>
				</div>
			</div>
		</PageShell>
	);
}
