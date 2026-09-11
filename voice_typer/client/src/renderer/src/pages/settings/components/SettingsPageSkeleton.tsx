// Settings page loading skeleton (full page, hub state).
//
// Mirrors the loaded Settings hub (`pages/Settings.tsx` +
// `components/settings/SettingsHub.tsx`): the real shell uses `gap-8`
// (not the data pages' gap-6), then PageHeading, then ONE
// `overflow-hidden rounded-lg border-border/5 bg-(--bg-subtle)
// divide-y divide-border/5` card of 9 section rows — each row is
// `flex w-full items-center gap-4 p-4` with a leading h-5 icon, a
// stacked title/description column, a trailing summary line and a
// chevron (exactly the 9 entries of `settingsSections.ts`).

import { HeadingSkeleton, PageShell } from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

const SECTION_ROW_IDS = [
	"settings-hub-row-0",
	"settings-hub-row-1",
	"settings-hub-row-2",
	"settings-hub-row-3",
	"settings-hub-row-4",
	"settings-hub-row-5",
	"settings-hub-row-6",
	"settings-hub-row-7",
	"settings-hub-row-8",
];

export function SettingsPageSkeleton() {
	return (
		<PageShell className="gap-8">
			<HeadingSkeleton />
			<div className="overflow-hidden rounded-lg border border-border/5 bg-(--bg-subtle) divide-y divide-border/5">
				{SECTION_ROW_IDS.map((id) => (
					<div key={id} className="flex w-full items-center gap-4 p-4">
						<Skeleton className="h-5 w-5 shrink-0" />
						<div className="flex min-w-0 flex-1 flex-col gap-0.5">
							<Skeleton className="h-5 w-28" />
							<Skeleton className="h-5 w-44" />
						</div>
						<Skeleton className="h-5 w-28 shrink-0" />
						<Skeleton className="h-4 w-4 shrink-0" />
					</div>
				))}
			</div>
		</PageShell>
	);
}
