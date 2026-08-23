// Low-confidence detection for the Home last-transcription preview.
//
// The Python dictation pipeline attaches a compact `quality` summary to
// the `transcription_final` push event when the active engine produced
// numeric per-segment confidence stats (Whisper batch path only — see
// `build_quality_summary` in `voice_typer/server/transcription.py`).
// This module folds that summary into a single boolean the preview card
// uses to decide whether to surface an inline "may be inaccurate"
// warning + Re-dictate action.
//
// Threshold rationale:
//   - `mean_logprob`: Whisper's per-segment `avg_logprob` is a log
//     probability; values approaching 0 are confident decodings. A mean
//     below -1.0 means the model assigned < ~37% probability to its own
//     tokens on average — empirically where misheard words become
//     common.
//   - `no_speech_prob_max`: the highest per-segment probability that a
//     segment was silent. Above 0.6 at least one transcribed segment
//     was likely noise/silence, a classic source of phantom words.
//
// The summary may be PARTIAL (each field is independent), so each
// signal is checked on its own and the result is their disjunction.
// Absent / non-numeric fields never trigger a warning by themselves.

import type { TranscriptionQualitySummary } from "@/types/ipc";

/** Below this mean log-probability the transcription is flagged. */
export const LOW_CONFIDENCE_MEAN_LOGPROB_THRESHOLD = -1.0;

/** Above this maximum no-speech probability the transcription is flagged. */
export const LOW_CONFIDENCE_NO_SPEECH_PROB_THRESHOLD = 0.6;

function isFiniteNumber(value: unknown): value is number {
	return typeof value === "number" && Number.isFinite(value);
}

/**
 * Whether the engine-reported confidence stats indicate a likely
 * inaccurate transcription. Returns `false` for `null`/`undefined`
 * (engines without stats) so high-confidence AND unknown-confidence
 * results both render without the warning.
 */
export function isLowConfidenceQuality(
	quality: TranscriptionQualitySummary | null | undefined,
): boolean {
	if (!quality) return false;
	const { mean_logprob, no_speech_prob_max } = quality;
	if (
		isFiniteNumber(mean_logprob) &&
		mean_logprob < LOW_CONFIDENCE_MEAN_LOGPROB_THRESHOLD
	) {
		return true;
	}
	if (
		isFiniteNumber(no_speech_prob_max) &&
		no_speech_prob_max > LOW_CONFIDENCE_NO_SPEECH_PROB_THRESHOLD
	) {
		return true;
	}
	return false;
}
