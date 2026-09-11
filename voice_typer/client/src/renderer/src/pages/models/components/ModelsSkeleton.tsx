// Models page loading skeleton (full page).
//
// Mirrors the loaded Models layout (`pages/Models.tsx` +
// `components/models/LocalModelsPanel.tsx`): page shell → heading with
// the trailing Import-Model action (the loaded PageHeading has one) →
// full-width Local/Cloud segmented control (rounded-xl card, p-1, two
// h-8 segments, active segment carries the bg-input indicator) →
// panel description + right-aligned Open-folder pill → the
// ModelGroupAccordion family cards: `rounded-xl border-border/5
// bg-(--bg-subtle)` rows with a `px-4 py-2` trigger (bare h-4 family
// logo + text-sm name + trailing plus icon), collapsed like the real
// accordions' default state.

import {
	HeadingSkeleton,
	PageShell,
	PillSkeleton,
} from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

const FAMILY_IDS = ["model-family-0", "model-family-1", "model-family-2"];

export function ModelsSkeleton() {
	return (
		<PageShell>
			<HeadingSkeleton action={<PillSkeleton className="w-28" />} />
			<div className="flex flex-col gap-3">
				<div className="pb-4">
					<div className="w-full rounded-xl border border-border/5 bg-(--bg-subtle) p-1">
						<div className="grid grid-cols-2 gap-1">
							<Skeleton className="h-8 rounded-md bg-input" />
							<Skeleton className="h-8 rounded-md" />
						</div>
					</div>
				</div>
			</div>
			<div className="flex flex-col gap-6">
				<div className="flex flex-col gap-4">
					<Skeleton className="h-5 w-3/4" />
					<div className="flex justify-end">
						<PillSkeleton className="w-36" />
					</div>
					<div className="flex flex-col gap-4">
						{FAMILY_IDS.map((id) => (
							<div
								key={id}
								className="w-full rounded-xl border border-border/5 bg-(--bg-subtle)"
							>
								<div className="flex items-center justify-between gap-2 px-4 py-2">
									<div className="flex min-w-0 items-center gap-2">
										<Skeleton className="h-4 w-10" />
										<Skeleton className="h-5 w-28" />
									</div>
									<Skeleton className="size-4 rounded" />
								</div>
							</div>
						))}
					</div>
				</div>
			</div>
		</PageShell>
	);
}
