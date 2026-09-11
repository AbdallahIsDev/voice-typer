// Microphone page loading skeleton (full page).
//
// Mirrors the loaded Microphone layout (`pages/Microphone.tsx`):
// page shell → heading → `gap-6` content group with
//
//   1. the ActiveMicrophoneCard test card (`rounded-xl border-border/5
//      bg-(--bg-subtle) p-4`: mic icon + name/description header, the
//      h-1.5 LevelBar track, the `mt-4` controls row) with the
//      PresetAccordionSelector underneath (`rounded-lg` card, single
//      uppercase-label + value-chip + chevron trigger row),
//   2. the AvailableMicrophonesList section (uppercase label + a
//      `rounded-lg` radio card of `px-4 py-2` rows: mic icon, name +
//      description lines, trailing radio).

import {
	HeadingSkeleton,
	PageShell,
	PillSkeleton,
	RadioSkeleton,
} from "@/components/feedback/skeletons";
import { Skeleton } from "@/components/ui/skeleton";

const DEVICE_ROW_IDS = ["mic-row-0", "mic-row-1", "mic-row-2"];

export function MicrophoneSkeleton() {
	return (
		<PageShell>
			<HeadingSkeleton />
			<div className="flex flex-col gap-6">
				<div className="rounded-xl border border-border/5 bg-(--bg-subtle) p-4">
					<div className="flex min-w-0 items-center gap-3">
						<Skeleton className="h-4 w-4 shrink-0" />
						<div className="flex min-w-0 flex-col gap-1">
							<Skeleton className="h-5 w-36" />
							<Skeleton className="h-4 w-48" />
						</div>
					</div>
					<div className="mt-3">
						<Skeleton className="h-1.5 w-full rounded-full" />
					</div>
					<div className="mt-4 flex items-center gap-3">
						<PillSkeleton className="w-28" />
						<Skeleton className="ms-auto h-4 w-12" />
					</div>
					<div className="mt-3 overflow-hidden rounded-lg border border-border/5 bg-(--bg-subtle)">
						<div className="flex items-center justify-between gap-3 px-4 py-2.5">
							<div className="flex min-w-0 items-center gap-2">
								<Skeleton className="h-4 w-36" />
							</div>
							<div className="flex shrink-0 items-center gap-2">
								<Skeleton className="h-6 w-20 rounded-md" />
								<Skeleton className="size-4" />
							</div>
						</div>
					</div>
				</div>
				<div className="flex flex-col gap-2">
					<Skeleton className="h-4 w-28 px-1" />
					<div className="rounded-lg border border-border/5 bg-(--bg-subtle)">
						<div className="divide-y divide-border/5">
							{DEVICE_ROW_IDS.map((id) => (
								<div key={id} className="flex items-center gap-3 px-4 py-2">
									<Skeleton className="h-4 w-4 shrink-0" />
									<div className="flex min-w-0 flex-1 flex-col gap-1">
										<Skeleton className="h-5 w-40" />
										<Skeleton className="h-4 w-56" />
									</div>
									<RadioSkeleton />
								</div>
							))}
						</div>
					</div>
				</div>
			</div>
		</PageShell>
	);
}
