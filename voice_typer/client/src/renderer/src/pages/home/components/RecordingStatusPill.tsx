import { memo } from "react";
import { cn } from "@/lib/utils";

/**
 * The coloured status pill shown at the top of the Home page. Pulses
 * while recording.
 *
 * NOT a live region: rendered as a plain `<div>` rather than `<output>`
 * so the implicit `status` live-region role does NOT announce
 * statusLabel changes. Home's single status live region is the dynamic
 * status line under the mic button (`<output aria-live="polite">` in
 * Home.tsx), and coarse state transitions are announced by App.tsx's
 * sr-only region ("Recording started." / "Ready." / …) — a live pill
 * would triple-announce every state change (e.g. READY → ERROR) along
 * side the line and the App-level region. (The original `<output>`
 * element silently re-introduced a live region despite this intent;
 * the extracted-file history is in Home.tsx's composition comment.)
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
				className="text-[0.6875rem] font-medium uppercase tracking-wide text-(--text-muted) transition-opacity duration-200 animate-fade-in"
			>
				{statusLabel}
			</span>
		</div>
	);
}

export default memo(RecordingStatusPill);
