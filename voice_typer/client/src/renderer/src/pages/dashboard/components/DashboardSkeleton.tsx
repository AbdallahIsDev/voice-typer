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

import { t } from "@/i18n/i18n";

export function DashboardSkeleton() {
	return (
		<section
			className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6"
			aria-label={t("analytics.loadingAria")}
			aria-busy="true"
		>
			{/* Heading skeleton */}
			<div className="space-y-2 pb-2">
				<div className="h-6 w-40 animate-pulse rounded bg-(--bg-subtle)" />
				<div className="h-4 w-64 animate-pulse rounded bg-(--bg-subtle)" />
			</div>
			<div className="flex justify-end pb-2">
				<div className="h-4 w-24 animate-pulse rounded bg-(--bg-subtle)" />
			</div>
			{/* 4 stat-card skeleton — mirrors the loaded card layout
			    (icon chip + value + label) so loading → loaded is
			    visually stable. */}
			<div className="grid grid-cols-2 gap-3 mt-6 md:grid-cols-4">
				{[0, 1, 2, 3].map((i) => (
					<div
						key={`stat-skel-${i}`}
						className="rounded-xl border border-border bg-(--bg-subtle) p-5 flex flex-col items-center justify-center gap-2.5"
					>
						<div className="rounded-lg bg-accent/10 p-2">
							<div className="h-4 w-4 animate-pulse rounded bg-(--bg-subtle)" />
						</div>
						<div className="h-6 w-12 animate-pulse rounded bg-(--bg-subtle)" />
						<div className="h-3 w-16 animate-pulse rounded bg-(--bg-subtle)" />
					</div>
				))}
			</div>
			{/* 7-day chart skeleton */}
			<div className="rounded-xl border border-border bg-(--bg-subtle) p-5 mt-8">
				<div className="flex items-center justify-between mb-5">
					<div className="space-y-1.5">
						<div className="h-4 w-32 animate-pulse rounded bg-(--bg-subtle)" />
						<div className="h-3 w-40 animate-pulse rounded bg-(--bg-subtle)" />
					</div>
					<div className="h-4 w-4 animate-pulse rounded bg-(--bg-subtle)" />
				</div>
				<div className="flex items-end justify-between gap-2 h-20">
					{[0, 1, 2, 3, 4, 5, 6].map((i) => (
						<div
							key={`bar-skel-${i}`}
							className="flex flex-1 flex-col items-center gap-2"
						>
							<div className="h-3 w-6 animate-pulse rounded bg-(--bg-subtle)" />
							<div
								className="w-full max-w-10 animate-pulse rounded-sm bg-(--bg-subtle)"
								style={{ height: `${20 + ((i * 7) % 40)}px` }}
							/>
							<div className="h-3 w-6 animate-pulse rounded bg-(--bg-subtle)" />
						</div>
					))}
				</div>
			</div>
			{/* Quick-info row skeleton — mirrors the loaded grid's
			    md:grid-cols-3 switch. */}
			<div className="grid grid-cols-1 gap-3 mt-8 md:grid-cols-3">
				{[0, 1, 2].map((i) => (
					<div
						key={`qi-skel-${i}`}
						className="rounded-lg border border-border bg-(--bg-subtle) p-3.5 flex items-center gap-3"
					>
						<div className="h-8 w-8 animate-pulse rounded-lg bg-(--bg-subtle)" />
						<div className="flex-1 space-y-1.5">
							<div className="h-3 w-12 animate-pulse rounded bg-(--bg-subtle)" />
							<div className="h-4 w-20 animate-pulse rounded bg-(--bg-subtle)" />
						</div>
					</div>
				))}
			</div>
		</section>
	);
}
