import type * as React from "react";

import { cn } from "#utils";

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
	return (
		<div
			data-slot="skeleton"
			className={cn("animate-pulse rounded-lg bg-surface-subtle", className)}
			{...props}
		/>
	);
}

export { Skeleton };
