//dashboard loading skeleton extracted from `pages/Dashboard.tsx`
// (lines ~418-488 of the pre-split file).
//
// Pure presentational component — no props, no hooks, no side effects.
// Mirrors the loaded dashboard layout (heading + 4 stat cards + 7-day
// chart placeholder + quick-info row) so the transition from "loading"
// to "loaded" is visually stable. The skeleton is only shown
// on the FIRST load (when `!data`) — subsequent refreshes keep the
// previous data visible while the new data loads (the `refreshing`
// flag drives the LastUpdatedIndicator spinner instead).

import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";

export function DashboardSkeleton() {
	return (
		<section
			className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6"
			aria-label={t("analytics.loadingAria")}
			aria-busy="true"
		>
			{/* Heading skeleton */}
			<div className="flex flex-col gap-2 pb-2">
				<Skeleton className="h-6 w-40 rounded bg-(--bg-subtle)" />
				<Skeleton className="h-4 w-64 rounded bg-(--bg-subtle)" />
			</div>
			<div className="flex flex-col gap-6">
				<div className="flex justify-end pb-2">
					<Skeleton className="h-4 w-24 rounded bg-(--bg-subtle)" />
				</div>
				{/* 4 stat-card skeleton — mirrors the loaded card layout
				    (icon chip + value + label) so loading → loaded is
				    visually stable. */}
				<div className="grid grid-cols-2 gap-3 md:grid-cols-4">
					{[0, 1, 2, 3].map((i) => (
						<div
							key={`stat-skel-${i}`}
							className="rounded-xl border border-border/5 bg-(--bg-subtle) p-4 flex flex-col items-center justify-center gap-2"
						>
							<div className="rounded-lg bg-accent/10 p-2">
								<Skeleton className="h-4 w-4 rounded bg-(--bg-subtle)" />
							</div>
							<Skeleton className="h-6 w-12 rounded bg-(--bg-subtle)" />
							<Skeleton className="h-3 w-16 rounded bg-(--bg-subtle)" />
						</div>
					))}
				</div>
				<div className="flex flex-col gap-8">
					{/* 7-day chart skeleton */}
					<div className="flex flex-col gap-4 rounded-xl border border-border/5 bg-(--bg-subtle) p-4">
						<div className="flex items-center justify-between">
							<div className="flex flex-col gap-2">
								<Skeleton className="h-4 w-32 rounded bg-(--bg-subtle)" />
								<Skeleton className="h-3 w-40 rounded bg-(--bg-subtle)" />
							</div>
							<Skeleton className="h-4 w-4 rounded bg-(--bg-subtle)" />
						</div>
						<div className="flex items-end justify-between gap-2 h-20">
							{[0, 1, 2, 3, 4, 5, 6].map((i) => (
								<div
									key={`bar-skel-${i}`}
									className="flex flex-1 flex-col items-center gap-2"
								>
									<Skeleton className="h-3 w-6 rounded bg-(--bg-subtle)" />
									<Skeleton
										className="w-full max-w-10 rounded-sm bg-(--bg-subtle)"
										style={{ height: `${20 + ((i * 7) % 40)}px` }}
									/>
									<Skeleton className="h-3 w-6 rounded bg-(--bg-subtle)" />
								</div>
							))}
						</div>
					</div>
					{/* Quick-info row skeleton — mirrors the loaded grid's
					    md:grid-cols-3 switch. */}
					<div className="grid grid-cols-1 gap-3 md:grid-cols-3">
						{[0, 1, 2].map((i) => (
							<div
								key={`qi-skel-${i}`}
								className="rounded-lg border border-border/5 bg-(--bg-subtle) p-4 flex items-center gap-3"
							>
								<Skeleton className="h-8 w-8 rounded-lg bg-(--bg-subtle)" />
								<div className="flex-1 flex flex-col gap-2">
									<Skeleton className="h-3 w-12 rounded bg-(--bg-subtle)" />
									<Skeleton className="h-4 w-20 rounded bg-(--bg-subtle)" />
								</div>
							</div>
						))}
					</div>
				</div>
			</div>
		</section>
	);
}
