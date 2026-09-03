// Unified page-level skeleton compositions.
//
// The app's loading UI has ONE primitive (`components/ui/skeleton.tsx`,
// shadcn Skeleton) and these page-shaped compositions built from it.
// Each composition mirrors the loaded page's container + heading +
// content layout so the loading → loaded transition is visually stable
// (no jump). Replaces the former per-page centered `Spinner` branches
// (History / Templates / Vocabulary / Models / Settings / Microphone)
// and the hand-rolled pulse-div skeletons (DashboardSkeleton /
// SettingsSkeleton now compose <Skeleton> too).
//
// Route fallback: `RouteSkeleton` renders while a lazy route chunk is
// streaming in (PageSwitch Suspense fallback).
//
// A11y: every composition renders as <output> with aria-busy="true" and
// the localized "loading" label (same contract the spinner branches
// used), so screen readers still announce the busy state.

import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

// Stable keys for the static, never-reordering skeleton rows. Lets the
// maps iterate ids directly (no array-index keys — lint/format safe).
const PLACEHOLDER_IDS = [
	"ph-0",
	"ph-1",
	"ph-2",
	"ph-3",
	"ph-4",
	"ph-5",
	"ph-6",
	"ph-7",
	"ph-8",
	"ph-9",
	"ph-10",
	"ph-11",
];

/** Standard page container — mirrors the data pages' `max-w-4xl px-16 pt-28` shell. */
function PageShell({
	children,
	className,
	label,
}: {
	children: ReactNode;
	className?: string;
	label: string;
}) {
	return (
		<output
			aria-busy="true"
			aria-label={label}
			className={cn(
				"mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6",
				className,
			)}
		>
			{children}
		</output>
	);
}

/** Heading block mirroring `PageHeading` (title + description). */
function HeadingSkeleton({
	titleWidth = "w-56",
	descriptionWidth = "w-80",
}: {
	titleWidth?: string;
	descriptionWidth?: string;
}) {
	return (
		<div className="flex flex-col gap-2">
			<Skeleton className={cn("h-7", titleWidth)} />
			<Skeleton className={cn("h-4", descriptionWidth)} />
		</div>
	);
}

/** Toolbar row (search / sort / actions) used by list pages. */
function ToolbarSkeleton() {
	return (
		<div className="flex w-full flex-wrap items-center gap-2">
			<Skeleton className="h-9 w-48 rounded-4xl" />
			<Skeleton className="h-9 w-28 rounded-4xl" />
			<Skeleton className="ms-auto h-9 w-32 rounded-4xl" />
		</div>
	);
}

/** One generic list row: leading lines + trailing pill. */
function ListRowSkeleton({ className }: { className?: string }) {
	return (
		<div
			className={cn(
				"flex items-center justify-between gap-4 rounded-xl border border-border/5 bg-(--bg-subtle) px-4 py-3",
				className,
			)}
		>
			<div className="flex min-w-0 flex-1 flex-col gap-2">
				<Skeleton className="h-4 w-2/5" />
				<Skeleton className="h-3 w-1/4" />
			</div>
			<Skeleton className="h-6 w-14 rounded-full" />
		</div>
	);
}

// ── Compositions ────────────────────────────────────────────────────

/**
 * Generic list page (History / Templates / Vocabulary): heading +
 * toolbar + N rows.
 */
export function ListPageSkeleton({
	rows = 6,
	className,
}: {
	rows?: number;
	className?: string;
}) {
	return (
		<PageShell label={t("a11y.loading")} className={className}>
			<HeadingSkeleton />
			<ToolbarSkeleton />
			<div className="flex flex-col gap-3">
				{PLACEHOLDER_IDS.slice(0, rows).map((id) => (
					<ListRowSkeleton key={id} />
				))}
			</div>
		</PageShell>
	);
}

/**
 * Models page: heading + panel tabs + 2-col grid of model cards.
 */
export function ModelsSkeleton({ className }: { className?: string }) {
	// Single-column accordion rows — mirrors the loaded Local tab's
	// ModelGroupAccordion layout (full-width family cards with a
	// leading logo chip + trailing action pill), not a 2-col grid that
	// would jump on load.
	return (
		<PageShell label={t("a11y.loading")} className={className}>
			<HeadingSkeleton />
			<Skeleton className="h-10 w-full max-w-md rounded-xl" />
			<div className="flex flex-col gap-3">
				{PLACEHOLDER_IDS.slice(0, 4).map((id) => (
					<div
						key={id}
						className="flex items-center justify-between gap-4 rounded-xl border border-border/5 bg-(--bg-subtle) p-4"
					>
						<div className="flex min-w-0 items-center gap-3">
							<Skeleton className="h-9 w-9 shrink-0 rounded-lg" />
							<div className="flex min-w-0 flex-1 flex-col gap-2">
								<Skeleton className="h-4 w-2/5" />
								<Skeleton className="h-3 w-1/4" />
							</div>
						</div>
						<Skeleton className="h-8 w-24 shrink-0 rounded-4xl" />
					</div>
				))}
			</div>
		</PageShell>
	);
}

/**
 * Settings page: heading + stacked setting rows (label left, control
 * right) — mirrors the section-row layout the loaded sub-pages use.
 */
export function SettingsPageSkeleton({ className }: { className?: string }) {
	return (
		<PageShell label={t("a11y.loading")} className={className}>
			<HeadingSkeleton />
			<div className="flex flex-col gap-8">
				{PLACEHOLDER_IDS.slice(0, 2).map((sid) => (
					<div key={sid} className="flex flex-col gap-3">
						<Skeleton className="h-5 w-40" />
						{PLACEHOLDER_IDS.slice(0, 4).map((id) => (
							<div
								key={id}
								className="flex items-center justify-between gap-4 rounded-xl border border-border/5 px-4 py-2"
							>
								<div className="flex items-center gap-2">
									<Skeleton className="h-4 w-28" />
									<Skeleton className="h-4 w-4" />
								</div>
								<Skeleton className="h-6 w-12" />
							</div>
						))}
					</div>
				))}
			</div>
		</PageShell>
	);
}

/**
 * Microphone page: heading + device-select row + level meter +
 * config rows.
 */
export function MicrophoneSkeleton({ className }: { className?: string }) {
	return (
		<PageShell label={t("a11y.loading")} className={className}>
			<HeadingSkeleton />
			<div className="flex flex-col gap-3">
				<Skeleton className="h-10 w-full rounded-xl" />
				<Skeleton className="h-14 w-full rounded-xl" />
			</div>
			<div className="flex flex-col gap-3">
				{PLACEHOLDER_IDS.slice(0, 3).map((id) => (
					<ListRowSkeleton key={id} />
				))}
			</div>
		</PageShell>
	);
}

/**
 * Neutral route-chunk fallback for PageSwitch's Suspense boundary.
 * Deliberately generic (heading + rows) — it shows for at most one
 * frame in practice because route chunks are prefetched at idle
 * (see router/prefetch.ts) and React.lazy caches resolved modules.
 */
export function RouteSkeleton() {
	return <ListPageSkeleton rows={4} />;
}
