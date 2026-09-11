// Shared skeleton primitives + the route-chunk fallback.
//
// The app's loading UI has ONE primitive (`components/ui/skeleton.tsx`,
// shadcn Skeleton) and these layout primitives. Every PAGE composes its
// own skeleton from them (`pages/<page>/components/*Skeleton.tsx`) so
// each loading state mirrors the page it replaces: structure, spacing,
// row counts and proportions come from the loaded UI, never from a
// generic template. Rules:
//
//   - dimensions mirror the real components (a toolbar pill is h-8 like
//     SortSelect's trigger, a switch placeholder is h-5 w-11 like
//     ui/switch.tsx, a text line matches its font's line box);
//   - page containers reuse the loaded shell classes verbatim so the
//     loading → loaded transition does not shift layout;
//   - every skeleton region is an <output aria-busy="true"> whose
//     accessible name is the localized loading label (the one contract
//     pinned by `__tests__/pages/loading-patterns.test.tsx`).
//
// `RouteSkeleton` is the single deliberate generic: it renders while a
// lazy route CHUNK is streaming in, for at most one frame in practice
// (chunks are prefetched at idle, see router/prefetch.ts), so it shows
// only the neutral page shell + heading block.

import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

// ── Region shells ───────────────────────────────────────────────────

/**
 * The accessible skeleton region every loading state renders inside.
 * `<output>` has the implicit `status` role; `aria-busy` marks the
 * busy state for assistive tech.
 */
export function SkeletonRegion({
	children,
	className,
	label,
}: {
	children: ReactNode;
	className?: string;
	label?: string;
}) {
	return (
		<output
			aria-busy="true"
			aria-label={label ?? t("a11y.loading")}
			className={className}
		>
			{children}
		</output>
	);
}

/** Page container, mirrors the data pages' `max-w-4xl px-16 pt-28` shell. */
export function PageShell({
	children,
	className,
	label,
}: {
	children: ReactNode;
	className?: string;
	label?: string;
}) {
	return (
		<SkeletonRegion
			label={label}
			className={cn(
				"mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-28 pb-6",
				className,
			)}
		>
			{children}
		</SkeletonRegion>
	);
}

// ── Atoms, dimensioned after the real controls ──────────────────────

/**
 * Toolbar button / select pill. Matches the `h-8` box the loaded
 * toolbar renders (SortSelect trigger is exactly h-8; `size="sm"`
 * Buttons land within 2px of it).
 */
export function PillSkeleton({ className }: { className?: string }) {
	return <Skeleton className={cn("h-8 rounded-4xl", className)} />;
}

/** Ghost `size="icon-xs"` action button placeholder (real: size-6). */
export function IconButtonSkeleton({ className }: { className?: string }) {
	return <Skeleton className={cn("size-6 rounded-md", className)} />;
}

/** ui/checkbox placeholder (real: size-4 rounded-[5px]). */
export function CheckboxSkeleton({ className }: { className?: string }) {
	return <Skeleton className={cn("size-4 rounded-[5px]", className)} />;
}

/** ui/radio-group item placeholder (real: size-4 rounded-full). */
export function RadioSkeleton({ className }: { className?: string }) {
	return <Skeleton className={cn("size-4 rounded-full", className)} />;
}

/** ui/switch placeholder (real: h-5 w-11 rounded-full). */
export function SwitchSkeleton({ className }: { className?: string }) {
	return <Skeleton className={cn("h-5 w-11 rounded-full", className)} />;
}

// ── Heading block ───────────────────────────────────────────────────

/**
 * Heading block mirroring `components/common/PageHeading.tsx`.
 *
 * Dimensions come from the real heading: the h1 is `text-2xl` (32px
 * line box → h-8) and the description is `text-sm` (20px → h-5). With
 * `action` set, renders the with-children variant: heading column left,
 * action placeholder(s) right: same wrap layout PageHeading uses.
 */
export function HeadingSkeleton({
	titleWidth = "w-56",
	descriptionWidth = "w-80",
	action,
}: {
	titleWidth?: string;
	descriptionWidth?: string;
	action?: ReactNode;
}) {
	const heading = (
		<div className="flex min-w-0 flex-col gap-2">
			<Skeleton className={cn("h-8", titleWidth)} />
			<Skeleton className={cn("h-5", descriptionWidth)} />
		</div>
	);
	if (action === undefined) {
		return heading;
	}
	return (
		<div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
			{heading}
			<div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>
		</div>
	);
}

// ── Route-chunk fallback ────────────────────────────────────────────

/**
 * Neutral fallback for PageSwitch's Suspense boundary (lazy route
 * chunk streaming). Deliberately generic, it shows for at most one
 * frame in practice because route chunks are prefetched at idle
 * (see router/prefetch.ts) and React.lazy caches resolved modules —
 * so it renders only the shell + heading, never a fake page body.
 */
export function RouteSkeleton() {
	return (
		<PageShell>
			<HeadingSkeleton />
		</PageShell>
	);
}
