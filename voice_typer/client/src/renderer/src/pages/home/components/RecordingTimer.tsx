import { memo, useEffect, useState } from "react";
import { t } from "@/i18n/i18n";

/**
 * The live MM:SS recording timer shown next to the status pill while
 * recording.
 *
 * Owns its own per-second `setInterval` + elapsed-seconds state so the
 * tick re-renders ONLY this leaf — previously the interval lived in
 * Home.tsx, so every second re-rendered the entire Home tree (stats,
 * activity list, share image, …) just to bump two digits. The component
 * is additionally wrapped in `React.memo` so unrelated Home re-renders
 * (state flips, event refreshes) skip it when `isRecording` is
 * unchanged.
 *
 * Rendered output is byte-identical to the previous inline span in
 * Home.tsx — including `role="timer"` + explicit `aria-live="off"`:
 * per WAI-ARIA the `timer` role only carries live="off" implicitly and
 * some screen readers announce role="timer" content changes anyway, so
 * the explicit attribute is what guarantees the per-second tick is
 * NEVER announced (Home's single status live region is the dynamic
 * line under the mic button).
 */
export interface RecordingTimerProps {
	/** Whether a recording is in progress. When false the timer renders nothing. */
	isRecording: boolean;
}

/** Format total seconds as zero-padded MM:SS (e.g. 65 → "01:05"). */
export function formatElapsedSeconds(totalSec: number): string {
	return `${String(Math.floor(totalSec / 60)).padStart(2, "0")}:${String(totalSec % 60).padStart(2, "0")}`;
}

export function RecordingTimer({ isRecording }: RecordingTimerProps) {
	// Elapsed seconds while recording — reset to 0 when a new recording
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
			className="font-mono text-sm tabular-nums text-(--text-muted)"
			role="timer"
			aria-live="off"
			aria-label={t("home.timerAria", { duration })}
		>
			{duration}
		</span>
	);
}

export default memo(RecordingTimer);
