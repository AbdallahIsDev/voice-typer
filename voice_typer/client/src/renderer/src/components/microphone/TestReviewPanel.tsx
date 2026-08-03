import { PlayIcon, RefreshIcon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface QualityData {
	volume_level: "good" | "low" | "very_low";
	volume_rms: number;
	peak_level: number;
	noise_level: "low" | "moderate" | "high";
	has_voice: boolean;
	has_clipping: boolean;
	detected_issues: string[];
	estimated_transcription_quality: number;
	silence_ratio: number;
}

interface TestReviewPanelProps {
	durationMs: number;
	quality: QualityData | null;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	playing: boolean;
	playingOriginal: boolean;
	onPlayEnhanced: () => void;
	onPlayOriginal: () => void;
	onStop: () => void;
	onRetest: () => void;
	hasFiltersEnabled: boolean;
}

/**
 * Fix 16: map backend `detected_issues` literal strings to i18n keys.
 *
 * The backend (`voice_typer/server/level_monitor.py`) emits a fixed set
 * of human-readable English strings for each detected issue. Without
 * this map, non-English users saw raw English issue text under the
 * "Detected Issues" heading. The map covers every backend-emitted
 * string; unknown strings fall through to the raw value (so future
 * backend additions don't render as empty/missing).
 */
const DETECTED_ISSUE_LITERALS: Record<string, string> = {
	"High background noise": "microphoneTest.detectedIssueCodes.high_noise",
	"Moderate background noise":
		"microphoneTest.detectedIssueCodes.moderate_noise",
	"Audio clipping detected": "microphoneTest.detectedIssueCodes.clipping",
	"Volume too low — speak closer to the microphone":
		"microphoneTest.detectedIssueCodes.volume_too_low",
	"Volume is low — consider raising input gain":
		"microphoneTest.detectedIssueCodes.volume_low",
	"No voice detected — try speaking during the test":
		"microphoneTest.detectedIssueCodes.no_voice",
};

/**
 * Translate a backend `detected_issues` literal into the user's locale.
 * Falls back to the raw string when the literal is not in the known map
 * (e.g. a newer backend emits a code we haven't catalogued yet) — this
 * preserves whatever information the backend did send rather than
 * dropping it silently.
 */
function translateDetectedIssue(raw: string): string {
	const key = DETECTED_ISSUE_LITERALS[raw];
	if (key) return t(key);
	return raw;
}

export function TestReviewPanel({
	durationMs,
	quality,
	testAudioBase64,
	rawAudioBase64,
	playing,
	playingOriginal,
	onPlayEnhanced,
	onPlayOriginal,
	onStop,
	onRetest,
	hasFiltersEnabled,
}: TestReviewPanelProps) {
	if (!testAudioBase64 && !rawAudioBase64) return null;

	return (
		<div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-4 space-y-3">
			{/* Header */}
			<div className="flex items-center justify-between">
				{" "}
				<div>
					<p className="text-sm font-semibold text-(--text-primary)">
						{t("microphoneTest.title")}
					</p>
					<p className="text-xs text-(--text-muted)">
						{t("microphoneTest.duration", {
							duration: (durationMs / 1000).toFixed(1),
						})}
					</p>
				</div>
				<Button
					variant="outline"
					size="sm"
					className="gap-1.5"
					onClick={onRetest}
				>
					<HugeiconsIcon
						icon={RefreshIcon}
						strokeWidth={1.625}
						className="h-3.5 w-3.5"
					/>
					{t("microphoneTest.retest")}
				</Button>
			</div>

			{/* Quality score */}
			{quality && (
				<>
					<div
						className="flex items-center justify-between"
						// BG-71: the quality summary is the primary live
						// result of a mic test — announce updates to AT
						// as one atomic polite region so screen-reader
						// users hear the full verdict, not fragments.
						aria-live="polite"
						aria-atomic="true"
					>
						<span className="text-xs font-medium text-(--text-muted)">
							{t("microphoneTest.estimatedQuality")}
						</span>
						<span
							className={`text-sm font-bold tabular-nums ${
								quality.estimated_transcription_quality >= 80
									? "text-success"
									: quality.estimated_transcription_quality >= 50
										? "text-warning"
										: "text-destructive"
							}`}
						>
							{quality.estimated_transcription_quality}%
						</span>
					</div>

					{/* Detailed metrics */}
					<div className="grid grid-cols-2 gap-2 text-xs">
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.volume")}
							</span>
							<div className="flex items-center gap-1.5">
								<span
									className={`w-1.5 h-1.5 rounded-full ${
										quality.volume_level === "good"
											? "bg-success"
											: "bg-warning"
									}`}
								/>
								<span>
									{quality.volume_level === "good"
										? t("microphoneTest.good")
										: quality.volume_level === "low"
											? t("microphoneTest.low")
											: t("microphoneTest.veryLow")}
								</span>
							</div>
						</div>
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.backgroundNoise")}
							</span>
							<div className="flex items-center gap-1.5">
								<span
									className={`w-1.5 h-1.5 rounded-full ${
										quality.noise_level === "low"
											? "bg-success"
											: quality.noise_level === "moderate"
												? "bg-warning"
												: "bg-destructive"
									}`}
								/>
								<span>
									{quality.noise_level === "low"
										? t("microphoneTest.lowNoise")
										: quality.noise_level === "moderate"
											? t("microphoneTest.moderateNoise")
											: t("microphoneTest.highNoise")}
								</span>
							</div>
						</div>
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.clipping")}
							</span>
							<span className="ms-1">
								{quality.has_clipping
									? t("microphoneTest.clippingDetected")
									: t("microphoneTest.clippingNone")}
							</span>
						</div>
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.voice")}
							</span>
							<span className="ms-1">
								{quality.has_voice
									? t("microphoneTest.voiceDetected")
									: t("microphoneTest.voiceNotDetected")}
							</span>
						</div>
					</div>

					{/* Detected issues */}
					{quality.detected_issues.length > 0 && (
						<div className="text-xs text-(--text-muted) space-y-0.5">
							<output
								className="font-medium text-warning"
								// BG-71: detected issues are a status
								// update — <output> (role=status)
								// announces them without stealing focus.
							>
								{t("microphoneTest.detectedIssues")}
							</output>
							{quality.detected_issues.map((issue) => {
								const translated = translateDetectedIssue(issue);
								return (
									<div key={issue} className="flex items-center gap-1">
										<span className="text-amber-500" aria-hidden="true">
											•
										</span>
										<span>{translated}</span>
									</div>
								);
							})}
						</div>
					)}
				</>
			)}

			{/* Playback controls */}
			<div className="flex flex-wrap items-center gap-2">
				{testAudioBase64 && hasFiltersEnabled && (
					<Button
						variant="outline"
						size="sm"
						className="gap-1.5"
						onClick={playing && !playingOriginal ? onStop : onPlayEnhanced}
					>
						<HugeiconsIcon
							icon={playing && !playingOriginal ? StopIcon : PlayIcon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
						{playing && !playingOriginal
							? t("microphoneTest.stop")
							: t("microphoneTest.playEnhanced")}
					</Button>
				)}

				{rawAudioBase64 && (
					<Button
						variant="outline"
						size="sm"
						className="gap-1.5"
						onClick={playing && playingOriginal ? onStop : onPlayOriginal}
					>
						<HugeiconsIcon
							icon={playing && playingOriginal ? StopIcon : PlayIcon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
						{playing && playingOriginal
							? t("microphoneTest.stop")
							: t("microphoneTest.playOriginal")}
					</Button>
				)}

				{!hasFiltersEnabled && testAudioBase64 && (
					<Button
						variant="outline"
						size="sm"
						className="gap-1.5"
						onClick={playing ? onStop : onPlayEnhanced}
					>
						<HugeiconsIcon
							icon={playing ? StopIcon : PlayIcon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
						{playing
							? t("microphoneTest.stop")
							: t("microphoneTest.playRecording")}
					</Button>
				)}
			</div>
		</div>
	);
}
