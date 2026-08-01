import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * ReadonlyRow — read-only label/value row for status displays.
 *
 * Distinct from {@link SettingRow}, which is designed for interactive
 * controls (it always emphasises the LABEL and renders the control as its
 * child). `ReadonlyRow` is for status rows where both sides are static
 * text — it lets callers choose which side to emphasise via the
 * `variant` prop so each call site can preserve its existing visual
 * rhythm:
 *
 *   - `value-emphasized` (default): muted label + prominent value. Used
 *     by PrewarmAndUpdates.tsx for status rows where the value is the
 *     primary read (Prewarm Status, Last Run, Cache Health, Installed
 *     Version, Latest Release).
 *   - `label-emphasized`: prominent label + muted value. Used by
 *     About.tsx where the label is the more important context
 *     (e.g. "App Version", "Python Backend", "Credits: Authors").
 *
 * Extracted from the local `Row` primitives that previously existed in
 * About.tsx and PrewarmAndUpdates.tsx.
 */
type ReadonlyRowVariant = "value-emphasized" | "label-emphasized";

interface ReadonlyRowProps {
	/** Static label text (always a string — never markup). */
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
				"flex items-center justify-between px-3.5 py-2.5",
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
						? "shrink-0 text-right text-(--text-muted)"
						: "font-medium text-(--text-primary)",
				)}
			>
				{value}
			</span>
		</div>
	);
}
