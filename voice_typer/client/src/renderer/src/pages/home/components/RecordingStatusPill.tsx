import { memo } from "react";
import { cn } from "@/lib/utils";

/**
 * The coloured status pill shown at the top of the Home page. Pulses
 * while recording. The `aria-live` announcement lives in App.tsx (a
 * single sr-only live region) — a duplicate `aria-live` on this `<output>`
 * would cause double-announcements, so it's intentionally omitted.
 *
 *  / : extracted from Home.tsx so the page file stays a
 * thin composition root. Behaviour + props are preserved byte-for-byte.
 */
export interface RecordingStatusPillProps {
	statusColor: string;
	statusLabel: string;
	isRecording: boolean;
}

export function RecordingStatusPill({
	statusColor,
	statusLabel,
	isRecording,
}: RecordingStatusPillProps) {
	return (
		<output className="flex items-center gap-2 animate-fade-in">
			<span
				className={cn(
					"h-2 w-2 rounded-full transition-colors duration-300",
					isRecording && "animate-pulse",
				)}
				style={{ backgroundColor: statusColor }}
				aria-hidden
			/>
			<span
				key={statusLabel}
				className="text-[11px] font-medium uppercase tracking-wide text-(--text-muted) transition-opacity duration-200 animate-fade-in"
			>
				{statusLabel}
			</span>
		</output>
	);
}

export default memo(RecordingStatusPill);
