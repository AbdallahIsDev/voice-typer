import { type ReactNode, useId } from "react";

interface SettingsSectionProps {
	title: string;
	description?: string;
	children: ReactNode;
}

export function SettingsSection({
	title,
	description,
	children,
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
			{/* Clean-card: no hard outer outline. The soft background +
			    row dividers still group the rows without a boxed frame. */}
			<div className="rounded-lg bg-(--bg-subtle) divide-y divide-border">
				{children}
			</div>
		</section>
	);
}
