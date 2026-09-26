import { t } from "@/i18n/i18n";

interface LiveQualityFeedbackProps {
	isRecording: boolean;
	elapsedSeconds: number;
	totalSeconds: number;
}

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
		// Spacing contract: rendered as a direct child of the
		// `ActiveMicrophoneCard` `flex flex-col gap-3` stack, which owns the
		// inter-child spacing, so no top margin here.
		<div className="text-center">
			{/* Timer, visual-only; rapid updates would spam SR if live */}
			<span className="text-xs font-mono tabular-nums text-muted-foreground">
				{t("microphoneTest.qualityFeedback.recording")}{" "}
				{formatTime(elapsedSeconds)} / {formatTime(totalSeconds)}
			</span>
		</div>
	);
}
