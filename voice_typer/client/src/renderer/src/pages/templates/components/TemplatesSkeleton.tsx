// Templates page loading skeleton (full page).
//
// Mirrors the loaded Templates layout (`pages/Templates.tsx`), which
// shares Vocabulary's page shell, CollectionToolbar and single
// columned list card. Row internals follow TemplateListRow instead:
// column 2 stacks the trigger line + a match-mode pill, column 3 is a
// single muted line, and the trailing action column has TWO ghost
// icon buttons.

import {
	CheckboxSkeleton,
	HeadingSkeleton,
	IconButtonSkeleton,
	PageShell,
	PillSkeleton,
} from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

const ROW_IDS = [
	"template-row-0",
	"template-row-1",
	"template-row-2",
	"template-row-3",
	"template-row-4",
	"template-row-5",
	"template-row-6",
	"template-row-7",
];

function TemplateRow() {
	return (
		<div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]">
			<CheckboxSkeleton className="self-start pt-0.5 sm:self-center sm:pt-0" />
			<div className="flex min-w-0 flex-col items-start gap-1">
				<Skeleton className="h-5 w-1/3" />
				<Skeleton className="h-4 w-16 rounded-full" />
			</div>
			<div className="col-start-2 flex min-w-0 items-center sm:col-start-auto">
				<Skeleton className="h-4 w-1/4" />
			</div>
			<div className="flex shrink-0 items-center justify-self-end gap-0.5">
				<IconButtonSkeleton />
				<IconButtonSkeleton />
			</div>
		</div>
	);
}

export function TemplatesSkeleton() {
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
							<TemplateRow key={id} />
						))}
					</div>
				</div>
			</div>
		</PageShell>
	);
}
