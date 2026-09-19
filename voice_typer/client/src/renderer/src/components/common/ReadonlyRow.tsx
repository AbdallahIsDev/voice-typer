import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type ReadonlyRowVariant = "value-emphasized" | "label-emphasized";

interface ReadonlyRowProps {
	/** Static label text (always a string, never markup). */
	label: string;
	/** Value content. May be a string, number, or ReactNode (e.g. a
	 *  `<StatusDot>` or `<CacheStatusBadge>`). */
	value: ReactNode;
	/** Visual emphasis. Defaults to `value-emphasized`. See the
	 *  component-level JSDoc for the rationale per variant. */
	variant?: ReadonlyRowVariant;
}

export function ReadonlyRow({
	label,
	value,
	variant = "value-emphasized",
}: ReadonlyRowProps) {
	const labelEmphasized = variant === "label-emphasized";
	return (
		<div
			className={cn(
				"flex items-center justify-between px-4 py-2",
				labelEmphasized ? "gap-6" : "gap-4",
			)}
		>
			<span
				className={cn(
					"text-sm",
					labelEmphasized
						? "font-medium text-(--text-primary)"
						: "text-(--text-muted)",
				)}
			>
				{label}
			</span>
			<span
				className={cn(
					"text-sm",
					labelEmphasized
						? "shrink-0 text-end text-(--text-muted)"
						: "font-medium text-(--text-primary)",
				)}
			>
				{value}
			</span>
		</div>
	);
}
