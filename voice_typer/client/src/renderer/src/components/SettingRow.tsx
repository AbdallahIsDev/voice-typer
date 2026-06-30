// src/renderer/src/components/SettingRow.tsx

import type { ReactNode } from "react";
import { useId } from "react";
import { cn } from "@/lib/utils";
import { InfoTooltip } from "./InfoTooltip";

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
	// UX-014: generate a unique ID for this row so the <label> can be
	// associated with its input via htmlFor.  This makes screen readers
	// announce the label when the input is focused, and lets clicking
	// the label focus the input.  The child input should use this ID
	// via the `useSettingRowId()` hook or by passing `id={...}` manually.
	const id = useId();
	return (
		<div
			className={cn(
				"flex items-start justify-between gap-6 px-3.5 py-2.5",
				align === "center" && "items-center",
			)}
		>
			<div className="flex min-w-0 items-center gap-2">
				<label
					htmlFor={id}
					className="text-sm font-medium text-(--text-primary) cursor-default"
				>
					{label}
				</label>
				{info && <InfoTooltip text={info} />}
			</div>
			<div className="shrink-0" data-setting-row-id={id}>
				{children}
			</div>
		</div>
	);
}
