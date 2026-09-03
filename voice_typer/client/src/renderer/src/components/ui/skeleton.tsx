import type * as React from "react";

import { cn } from "#utils";

/**
 * Skeleton — the app's single loading primitive (shadcn/ui).
 *
 * Every "content is loading" state in the app renders Skeletons shaped
 * like the content they replace (see `components/feedback/skeletons.tsx`
 * for the page-level compositions). Inline action-progress indicators
 * (e.g. a download button's glyph) keep using `Spinner` — a Skeleton is
 * for content placeholders, not button feedback.
 *
 * Theme: `--bg-subtle` tracks the active theme exactly like the
 * pre-existing hand-rolled skeletons (DashboardSkeleton /
 * SettingsSkeleton), so this component is a drop-in for those divs.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
	return (
		<div
			data-slot="skeleton"
			className={cn("animate-pulse rounded-md bg-(--bg-subtle)", className)}
			{...props}
		/>
	);
}

export { Skeleton };
