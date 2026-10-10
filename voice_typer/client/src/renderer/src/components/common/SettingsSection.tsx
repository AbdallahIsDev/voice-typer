import type { ReactNode } from "react";
import { useId } from "react";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { TooltipProvider } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface SettingsSectionProps {
	title: string;
	/** Card help: ? tooltip beside the title. Page-level headings can
	 *  pass `descriptionMode="text"` to keep the subtitle as body copy. */
	description?: string;
	descriptionMode?: "tooltip" | "text";
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
	descriptionMode = "tooltip",
	children,
	action,
	cardClassName,
}: SettingsSectionProps) {
	// Named landmark for SR navigation (WCAG 2.4.6 / 1.3.1 / 4.1.2).
	const headingId = useId();
	return (
		<section aria-labelledby={headingId} className="flex flex-col gap-4">
			<div className="flex items-start justify-between gap-4">
				<div className="flex flex-col gap-1">
					<div className="flex items-center gap-2">
						<h2
							id={headingId}
							className="font-sans text-lg font-medium text-foreground"
						>
							{title}
						</h2>
						{description && descriptionMode === "tooltip" && (
							// Own provider so SettingsSection works standalone (tests
							// and About) without requiring a page-level TooltipProvider.
							<TooltipProvider delayDuration={200}>
								<InfoTooltip text={description} contextLabel={title} />
							</TooltipProvider>
						)}
					</div>
					{description && descriptionMode === "text" && (
						<p className="text-sm text-muted-foreground">{description}</p>
					)}
				</div>
				{action}
			</div>
			<div
				className={cn(
					"rounded-lg border border-border/8 bg-surface-subtle divide-y divide-border/8",
					cardClassName,
				)}
			>
				{children}
			</div>
		</section>
	);
}
