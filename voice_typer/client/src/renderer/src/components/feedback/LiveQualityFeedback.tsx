import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface LiveQualityFeedbackProps {
	level: number;
	peak: number;
	isRecording: boolean;
	elapsedSeconds: number;
	totalSeconds: number;
}

export function LiveQualityFeedback({
	level,
	peak,
	isRecording,
	elapsedSeconds,
	totalSeconds,
}: LiveQualityFeedbackProps) {
	if (!isRecording) return null;

	const hasVoice = peak > 0.05;
	const _volumeGood = level > 0.02 && level < 0.7;
	const volumeLow = level <= 0.02 && level > 0.005;
	const volumeVeryLow = level <= 0.005;
	const tooLoud = peak > 0.9;

	const formatTime = (s: number) => {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
	};

	// A11Y-5: live region announces quality warnings to screen-reader users.
	// aria-atomic="true" ensures the entire message is re-read on each change
	// (otherwise SR may only speak the diff). The timer is intentionally NOT
	// wrapped in role="timer" / aria-live because rapid per-second updates
	// would spam the SR broadcast channel — the polite status region already
	// announces meaningful state transitions (recording, voice detected, warnings).
	return (
		<div
			className="mt-2 space-y-2"
			role="status"
			aria-live="polite"
			aria-atomic="true"
		>
			{/* Timer — visual-only; rapid updates would spam SR if live */}
			<div className="text-center">
				<span className="text-xs font-mono tabular-nums text-(--text-muted)">
					{t("microphoneTest.qualityFeedback.recording")}{" "}
					{formatTime(elapsedSeconds)} / {formatTime(totalSeconds)}
				</span>
			</div>

			{/* Voice detected indicator */}
			<div className="flex items-center justify-center gap-3 text-xs">
				<span className="flex items-center gap-1.5">
					<span
						className={cn(
							"w-1.5 h-1.5 rounded-full animate-pulse",
							hasVoice
								? "bg-green-500 shadow-[0_0_4px_rgba(34,197,94,0.6)]"
								: "bg-(--text-muted)/30",
						)}
					/>
					{hasVoice
						? t("microphoneTest.qualityFeedback.voiceDetected")
						: t("microphoneTest.qualityFeedback.waiting")}
				</span>

				{/* Quality indicator — NF-R15-11: bumped text-amber-500 → text-amber-700
				    and text-green-500 → text-emerald-700 for WCAG AA contrast. */}
				{hasVoice && !tooLoud && (
					<span className="text-emerald-700 dark:text-emerald-400">
						{t("microphoneTest.qualityFeedback.excellent")}
					</span>
				)}
				{hasVoice && tooLoud && (
					<span className="text-amber-700 dark:text-amber-400">
						{t("microphoneTest.qualityFeedback.tooHigh")}
					</span>
				)}
				{volumeVeryLow && !hasVoice && (
					<span className="text-amber-700 dark:text-amber-400">
						{t("microphoneTest.qualityFeedback.tooLow")}
					</span>
				)}
				{volumeLow && !hasVoice && (
					<span className="text-amber-700 dark:text-amber-400">
						{t("microphoneTest.qualityFeedback.lowVolume")}
					</span>
				)}
			</div>
		</div>
	);
}
