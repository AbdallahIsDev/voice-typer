import { getVolumeTier } from "@/components/feedback/LevelBar";
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

	// Classify the level/peak pair through the shared
	// ``getVolumeTier`` helper (defined in LevelBar.tsx) so the
	// bar and the textual feedback agree on what counts as
	// "clipping" / "healthy" / "low" / "silent".  Previously each
	// component hard-coded its own thresholds.
	const tier = getVolumeTier(level, peak);
	// ``hasVoice`` is kept as a separate signal for the "voice
	// detected" indicator dot — it gates the pulse animation
	// independently of which quality message is shown.
	const hasVoice = peak > 0.05;

	const formatTime = (s: number) => {
		const m = Math.floor(s / 60);
		const sec = Math.floor(s % 60);
		return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
	};

	// Live region announces quality warnings to screen-reader users.
	// aria-atomic="true" ensures the entire message is re-read on each change
	// (otherwise SR may only speak the diff). The timer is intentionally NOT
	// wrapped in role="timer" / aria-live because rapid per-second updates
	// would spam the SR broadcast channel — the polite status region already
	// announces meaningful state transitions (recording, voice detected, warnings).
	return (
		<output className="mt-2 space-y-2" aria-live="polite" aria-atomic="true">
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
							// Replace ``bg-green-500`` (hardcoded
							// Tailwind palette green) with the theme's
							// ``--primary`` token so the dot matches the
							// user's chosen accent colour in every theme
							// (Nord blue, Dracula purple, etc.) and the
							// glow is no longer hardwired to green-500 RGB.
							hasVoice ? "bg-primary" : "bg-(--text-muted)/30",
						)}
					/>
					{hasVoice
						? t("microphoneTest.qualityFeedback.voiceDetected")
						: t("microphoneTest.qualityFeedback.waiting")}
				</span>

				{/* Quality indicator — replace the hardcoded
                                    emerald / amber palette with CSS-variable tokens so the
                                    feedback adapts to every theme (the previous
                                    ``text-emerald-700 dark:text-emerald-400`` clung to a
                                    fixed green even in themes like Dracula where the
                                    primary accent is purple).  ``--primary`` carries the
                                    "healthy" semantic and ``--destructive`` carries the
                                    "warning" semantic, matching the LevelBar colour
                                    ladder so the two UIs agree. */}
				{tier === "good" && (
					<span className="text-primary">
						{t("microphoneTest.qualityFeedback.excellent")}
					</span>
				)}
				{tier === "loud" && (
					<span className="text-destructive">
						{t("microphoneTest.qualityFeedback.tooHigh")}
					</span>
				)}
				{tier === "silent" && (
					<span className="text-destructive">
						{t("microphoneTest.qualityFeedback.tooLow")}
					</span>
				)}
				{tier === "low" && (
					<span className="text-destructive">
						{t("microphoneTest.qualityFeedback.lowVolume")}
					</span>
				)}
			</div>
		</output>
	);
}
