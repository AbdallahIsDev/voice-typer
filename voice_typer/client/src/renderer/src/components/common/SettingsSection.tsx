import { type ReactNode, useId } from "react";
import { cn } from "@/lib/utils";

interface SettingsSectionProps {
	title: string;
	description?: string;
	children: ReactNode;
	/** Optional action rendered at the end of the heading row (e.g. a
	 *  header button like "Copy diagnostics"). */
	action?: ReactNode;
	/** Optional override for the card wrapper classes (e.g. a tinted /
	 *  bordered variant to visually distinguish one section's rows). */
	cardClassName?: string;
}

export function SettingsSection({
	title,
	description,
	children,
	action,
	cardClassName,
}: SettingsSectionProps) {
	// `<section>` is an ARIA landmark. WCAG 2.4.6 / SC 1.3.1 / SC 4.1.2
	// require each landmark to expose a programmatically-determinable
	// name. We generate a stable id for the visible `<h2>` and reference
	// it via `aria-labelledby` so SR users can navigate to this section
	// by name (e.g. "Microphone settings, region") rather than hearing
	// a generic "region" announcement.
	const headingId = useId();
	return (
		<section aria-labelledby={headingId} className="space-y-4">
			<div className="flex items-start justify-between gap-4">
				<div className="space-y-1">
					<h2
						id={headingId}
						className="font-sans text-lg font-semibold text-(--text-primary)"
					>
						{title}
					</h2>
					{description && (
						<p className="text-sm text-(--text-muted)">{description}</p>
					)}
				</div>
				{action}
			</div>
			{/* Section card: soft background + row dividers + a subtle
			    border so every section reads as a consistent card (About
			    and Settings share this component, so the border treatment
			    is uniform across the app). */}
			<div
				className={cn(
					"rounded-lg border border-border/5 bg-(--bg-subtle) divide-y divide-border/10",
					cardClassName,
				)}
			>
				{children}
			</div>
		</section>
	);
}
