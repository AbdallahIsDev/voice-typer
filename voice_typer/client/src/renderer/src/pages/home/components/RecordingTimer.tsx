import { memo, useEffect, useState } from "react";
import { t } from "@/i18n/i18n";

export interface RecordingTimerProps {
	/** Whether a recording is in progress. When false the timer renders nothing. */
	isRecording: boolean;
}

/** Format total seconds as zero-padded MM:SS (e.g. 65 → "01:05"). */
export function formatElapsedSeconds(totalSec: number): string {
	return `${String(Math.floor(totalSec / 60)).padStart(2, "0")}:${String(totalSec % 60).padStart(2, "0")}`;
}

export function RecordingTimer({ isRecording }: RecordingTimerProps) {
	// Elapsed seconds while recording, reset to 0 when a new recording
	// starts (and when recording stops, so the next start begins at zero).
	const [elapsedSec, setElapsedSec] = useState(0);
	useEffect(() => {
		if (!isRecording) {
			setElapsedSec(0);
			return;
		}
		setElapsedSec(0);
		const id = window.setInterval(() => {
			setElapsedSec((s) => s + 1);
		}, 1000);
		return () => window.clearInterval(id);
	}, [isRecording]);

	if (!isRecording) return null;

	const duration = formatElapsedSeconds(elapsedSec);
	return (
		<span
			className="font-mono text-sm tabular-nums text-muted-foreground"
			role="timer"
			aria-live="off"
			aria-label={t("home.timerAria", { duration })}
		>
			{duration}
		</span>
	);
}

export default memo(RecordingTimer);
