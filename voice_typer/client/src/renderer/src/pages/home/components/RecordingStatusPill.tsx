import { memo } from "react";
import { cn } from "@/lib/utils";

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
		<div className="flex items-center gap-2 animate-fade-in">
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
				className="text-[0.6875rem] font-medium uppercase tracking-wide text-muted-foreground transition-opacity duration-200 animate-fade-in"
			>
				{statusLabel}
			</span>
		</div>
	);
}

export default memo(RecordingStatusPill);
