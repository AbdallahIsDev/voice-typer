import { PlayIcon, RefreshIcon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { AudioPreset } from "@/components/microphone/AudioPresetSelector";
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
	/** Transcription of the test recording (null/undefined = none). */
	transcription?: string | null;
	/** True when no speech model is loaded, so no transcription can exist. */
	transcriptionUnavailable?: boolean;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	playing: boolean;
	playingOriginal: boolean;
	onPlayEnhanced: () => void;
	onPlayOriginal: () => void;
	onStop: () => void;
	onRetest: () => void;
	hasFiltersEnabled: boolean;
	/**
	 * (XA-5-8): optional callback wired to ``handlePresetChange`` in the
	 * parent. When provided, detected-noise issues render a one-click
	 * "Apply Noisy Room preset" CTA alongside the recommendation text.
	 * When absent, only the recommendation text is shown (still satisfies
	 * the XA-5-8 spirit — every detected issue has an actionable
	 * recommendation, even without the one-click apply).
	 */
	onApplyPreset?: (preset: AudioPreset) => void;
	/** Current preset so the CTA can disable when already applied. */
	currentPreset?: AudioPreset;
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

/**
 * (XA-5-8): per-issue recommendation + optional one-click CTA. Maps each
 * known detected-issue code to:
 *   • ``text`` — the actionable recommendation (always rendered).
 *   • ``applyPreset`` — when present AND the parent wired
 *     ``onApplyPreset``, the row renders a one-click CTA button that
 *     invokes the parent's preset-applier (e.g. "Apply Noisy Room
 *     preset" for ``high_noise``).
 *
 * Recommendations are deliberately concrete actions ("try the Noisy Room
 * preset", "speak closer to the microphone", "lower your input gain") —
 * the original panel surfaced the issue label alone, leaving the user
 * with no actionable next step. Every recommendation is a single
 * actionable sentence.
 *
 * Lookup is keyed by the canonical i18n-issue-code KEY (the
 * ``microphoneTest.detectedIssueCodes.*`` suffix), NOT by the backend's
 * raw English literal — that way the recommendation lookup is locale-
 * independent (the same code resolves in every locale) and survives a
 * backend rewording.
 */
interface IssueRecommendation {
	text: string;
	applyPreset?: AudioPreset;
}

function getIssueRecommendation(rawIssue: string): IssueRecommendation | null {
	const codeKey = DETECTED_ISSUE_LITERALS[rawIssue];
	if (!codeKey) return null;
	// Strip the i18n key prefix to get the issue code suffix
	// (e.g. "microphoneTest.detectedIssueCodes.high_noise" → "high_noise").
	const code = codeKey.split(".").pop();
	if (!code) return null;
	switch (code) {
		case "high_noise":
			return {
				text: t("microphoneTest.recommendations.high_noise"),
				applyPreset: "noisy_room",
			};
		case "moderate_noise":
			return {
				text: t("microphoneTest.recommendations.moderate_noise"),
				applyPreset: "noisy_room",
			};
		case "clipping":
			return {
				text: t("microphoneTest.recommendations.clipping"),
			};
		case "volume_too_low":
			return {
				text: t("microphoneTest.recommendations.volume_too_low"),
			};
		case "volume_low":
			return {
				text: t("microphoneTest.recommendations.volume_low"),
			};
		case "no_voice":
			return {
				text: t("microphoneTest.recommendations.no_voice"),
			};
		default:
			return null;
	}
}

export function TestReviewPanel({
	durationMs,
	quality,
	transcription,
	transcriptionUnavailable,
	testAudioBase64,
	rawAudioBase64,
	playing,
	playingOriginal,
	onPlayEnhanced,
	onPlayOriginal,
	onStop,
	onRetest,
	hasFiltersEnabled,
	onApplyPreset,
	currentPreset,
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

			{/* Test transcription — the primary "what did it hear" result.
			    Rendered when the backend produced text; when no speech
			    model is loaded (``transcriptionUnavailable``) a localized
			    explanation names the actual state instead of silence. */}
			{(transcription || transcriptionUnavailable) && (
				<div className="space-y-1">
					<p className="text-xs font-medium text-(--text-muted)">
						{t("microphone.youSaid")}
					</p>
					{transcription ? (
						<p
							className="text-sm text-(--text-primary)"
							data-testid="test-transcription"
						>
							{transcription}
						</p>
					) : (
						transcriptionUnavailable && (
							<p
								className="text-xs text-(--text-muted)"
								data-testid="test-transcription-unavailable"
							>
								{t("microphone.transcriptionUnavailable")}
							</p>
						)
					)}
				</div>
			)}

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
						<div className="text-xs text-(--text-muted) space-y-1">
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
								const recommendation = getIssueRecommendation(issue);
								const applyPreset = recommendation?.applyPreset;
								return (
									<div
										key={issue}
										className="space-y-0.5"
										data-testid="detected-issue-row"
									>
										<div className="flex items-center gap-1">
											<span className="text-warning" aria-hidden="true">
												•
											</span>
											<span>{translated}</span>
										</div>
										{/* (XA-5-8): per-issue Recommended
                                                                                        action block. Renders whenever a
                                                                                        recommendation text exists for the
                                                                                        detected issue. The one-click CTA
                                                                                        button renders when BOTH the
                                                                                        recommendation has an applyPreset
                                                                                        AND the parent wired onApplyPreset
                                                                                        AND that preset isn't already the
                                                                                        active one (no-op CTA would be
                                                                                        misleading). */}
										{recommendation && (
											<div className="ms-3 mt-0.5 flex flex-wrap items-center gap-2 rounded-md border-l-2 border-warning/40 bg-warning/5 px-2 py-1 text-(--text-muted)">
												<span
													className="text-[11px] leading-snug"
													data-testid="issue-recommendation"
												>
													{recommendation.text}
												</span>
												{applyPreset &&
													onApplyPreset &&
													currentPreset !== applyPreset && (
														<Button
															variant="outline"
															size="sm"
															className="h-6 px-2 text-[11px]"
															onClick={() => onApplyPreset(applyPreset)}
															data-testid="issue-apply-preset"
														>
															{t(
																"microphoneTest.recommendations.applyNoisyRoom",
															)}
														</Button>
													)}
											</div>
										)}
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
