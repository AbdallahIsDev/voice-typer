// src/renderer/src/components/common/SettingRow.tsx

import type { ReactNode } from "react";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { cn } from "@/lib/utils";

interface SettingRowProps {
	label: string;
	info?: string;
	children: ReactNode;
	align?: "start" | "center";
}

export function SettingRow({
	label,
	info,
	children,
	align = "center",
}: SettingRowProps) {
	// UX-014: the <span> below is purely visual — it does NOT use
	// ``htmlFor`` because there is no shared ID between the label
	// and the child input.  Children must provide their own
	// ``aria-label`` (or be wrapped in a ``<label>`` themselves) so
	// screen readers announce the setting name when the input is
	// focused.  A previous version of this component used ``<label>``
	// with ``useId()``, but that ID was never consumed by any child,
	// leaving ``htmlFor`` pointing at a non-existent element.
	// Using a <span> avoids the Biome ``a11y/noLabelWithoutControl``
	// lint violation.
	return (
		<div
			className={cn(
				"flex items-start justify-between gap-6 px-3.5 py-2.5",
				align === "center" && "items-center",
			)}
		>
			<div className="flex min-w-0 items-center gap-2">
				<span className="text-sm font-medium text-(--text-primary) cursor-default">
					{label}
				</span>
				{info && <InfoTooltip text={info} />}
			</div>
			<div className="shrink-0">{children}</div>
		</div>
	);
}
