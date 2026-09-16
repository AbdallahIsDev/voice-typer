import { PlayIcon, RefreshIcon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import type { AudioPreset } from "@/lib/utils/audioPresets";

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
	 * Optional callback wired to ``handlePresetChange`` in the
	 * parent. When provided, KNOWN detected-noise issues render a
	 * one-click "Apply Noisy Room preset" CTA alongside the
	 * recommendation text. When absent, only the recommendation text
	 * is shown for known issues. Unknown future backend strings render
	 * their translated-or-raw label with no recommendation and no CTA
	 * (fallback contract, never a crash).
	 */
	onApplyPreset?: (preset: AudioPreset) => void;
	/** Current preset so the CTA can disable when already applied. */
	currentPreset?: AudioPreset;
}

/**
 * Fix 16: map backend `detected_issues` literal strings to i18n keys.
 *
 * The backend (`voice_typer/server/level_monitor/test_recording.py`) emits a fixed set
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
	"Volume too low, speak closer to the microphone":
		"microphoneTest.detectedIssueCodes.volume_too_low",
	"Volume is low, consider raising input gain":
		"microphoneTest.detectedIssueCodes.volume_low",
	"No voice detected, try speaking during the test":
		"microphoneTest.detectedIssueCodes.no_voice",
};

/**
 * Translate a backend `detected_issues` literal into the user's locale.
 * Falls back to the raw string when the literal is not in the known map
 * (e.g. a newer backend emits a code we haven't catalogued yet), this
 * preserves whatever information the backend did send rather than
 * dropping it silently.
 */
function translateDetectedIssue(raw: string): string {
	const key = DETECTED_ISSUE_LITERALS[raw];
	if (key) return t(key);
	return raw;
}

/**
 * Per-issue recommendation + optional one-click CTA. Maps each
 * known detected-issue code to:
 *   • ``text``, the actionable recommendation (always rendered).
 *   • ``applyPreset``, when present AND the parent wired
 *     ``onApplyPreset``, the row renders a one-click CTA button that
 *     invokes the parent's preset-applier (e.g. "Apply Noisy Room
 *     preset" for ``high_noise``).
 *
 * Recommendations are deliberately concrete actions ("try the Noisy Room
 * preset", "speak closer to the microphone", "lower your input gain") —
 * the original panel surfaced the issue label alone, leaving the user
 * with no actionable next step. Every KNOWN-issue recommendation is a
 * single actionable sentence. Unknown future backend strings have no
 * recommendation (``getIssueRecommendation`` returns null): the row
 * renders the label only, with no recommendation text and no CTA.
 *
 * Lookup is keyed by the canonical i18n-issue-code KEY (the
 * ``microphoneTest.detectedIssueCodes.*`` suffix), NOT by the backend's
 * raw English literal, that way the recommendation lookup is locale-
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
	const hasVerdict =
		durationMs > 0 ||
		quality !== null ||
		(transcription != null && transcription !== "") ||
		transcriptionUnavailable === true;
	if (!testAudioBase64 && !rawAudioBase64 && !hasVerdict) return null;

	return (
		// Spacing contract: the only production parent (`ActiveMicrophoneCard`)
		// is a `flex flex-col gap-3` stack that owns inter-child spacing, so this
		// panel carries no top margin of its own.
		<div className="flex flex-col gap-4 rounded-xl border border-border/10 bg-(--bg-subtle) p-4">
			{/* Standard card surface (C-MIC-6): subtle bg + card border,
			    no tint. Spacing is parent gap only (C-UI-10). */}
			{/* Header */}
			<div className="flex items-center justify-between">
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
					className="gap-2"
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

			{/* Test transcription, the primary "what did it hear" result.
                            Rendered when the backend produced text; when no speech
                            model is loaded (``transcriptionUnavailable``) a localized
                            explanation names the actual state instead of silence. */}
			{(transcription || transcriptionUnavailable) && (
				<div className="flex flex-col gap-1">
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
						// result of a mic test, announce updates to AT
						// as one atomic polite region so screen-reader
						// users hear the full verdict, not fragments.
						aria-live="polite"
						aria-atomic="true"
					>
						<span className="text-xs font-medium text-(--text-muted)">
							{t("microphoneTest.estimatedQuality")}
						</span>
						{/* HONEST-METRIC INVARIANT: without a loaded speech model the
                                                 transcription-quality estimate cannot be computed, showing a
                                                 numeric score would fabricate a result from absent data (the
                                                 old bug rendered a false "0%"). Render an explicit
                                                 not-applicable state instead. */}
						{transcriptionUnavailable ? (
							<span className="text-sm font-bold text-(--text-muted)">
								{t("microphoneTest.qualityFeedback.qualityNotApplicable")}
							</span>
						) : (
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
						)}
					</div>

					{/* The per-metric grid (Volume / Noise / Clipping / Voice)
					    was removed: every non-good metric state already
					    surfaces as a detected issue below with an actionable
					    recommendation, so the grid duplicated the issues list
					    without adding information. The score row above + the
					    issues list below are the complete verdict. */}

					{/* Detected issues */}
					{quality.detected_issues.length > 0 && (
						<div
							className="flex flex-col gap-1 text-xs text-(--text-muted)"
							aria-live="polite"
							aria-atomic="true"
						>
							<output
								className="font-medium text-warning"
								// BG-71: detected issues are a status
								// update, <output> (role=status)
								// announces them without stealing focus.
								// The rows below sit inside this polite
								// atomic region so SR hears the issue
								// text, not just the heading.
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
										className="flex flex-col gap-0.5"
										data-testid="detected-issue-row"
									>
										<div className="flex items-center gap-1">
											<span className="text-warning" aria-hidden="true">
												•
											</span>
											<span>{translated}</span>
										</div>
										{/* Per-issue Recommended
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
											<div className="ms-4 flex flex-wrap items-center gap-2 text-(--text-muted)">
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
						className="gap-2"
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
						className="gap-2"
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
						className="gap-2"
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
