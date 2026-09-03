import { t } from "@/i18n/i18n";

interface LiveQualityFeedbackProps {
	isRecording: boolean;
	elapsedSeconds: number;
	totalSeconds: number;
}

/**
 * Single test-timer readout: `Recording MM:SS / MM:SS` progressing
 * 00:00 → 00:10. This is THE one time display during a test — the
 * redundant voice-quality status line ("Waiting for voice…"/"Voice
 * Detected"/"Low volume") that used to sit under it was removed: the
 * live LevelBar already communicates input level continuously, so a
 * second textual indicator duplicated it and flickered noisily.
 *
 * The timer is rendered OUTSIDE any aria-live region on purpose — rapid
 * per-second updates would spam screen-reader broadcast channels.
 */
export function LiveQualityFeedback({
	isRecording,
	elapsedSeconds,
	totalSeconds,
}: LiveQualityFeedbackProps) {
	if (!isRecording) return null;

	const formatTime = (s: number) => {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
	};

	return (
		<div className="mt-2 text-center">
			{/* Timer — visual-only; rapid updates would spam SR if live */}
			<span className="text-xs font-mono tabular-nums text-(--text-muted)">
				{t("microphoneTest.qualityFeedback.recording")}{" "}
				{formatTime(elapsedSeconds)} / {formatTime(totalSeconds)}
			</span>
		</div>
	);
}
