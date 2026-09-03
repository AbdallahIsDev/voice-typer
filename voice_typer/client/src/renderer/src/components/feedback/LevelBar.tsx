import type { CSSProperties } from "react";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface LevelBarProps {
	/** RMS level 0–1 */
	level: number;
	/** Whether audio playback is active (freezes the bar) */
	playing: boolean;
}

// ── Centralised audio-quality thresholds ──────────────────────────────
//
// Both ``LevelBar`` and
// ``LiveQualityFeedback`` need to classify the current RMS / peak pair
// into a qualitative band.  Previously each component hard-coded its
// own thresholds (``lvl > 0.7`` here, ``peak > 0.9`` there, etc.) so
// the two UIs could disagree about whether the user was clipping.
//
// ``getVolumeTier`` is the single source of truth.  The thresholds
// were chosen to preserve every existing visual / textual behaviour:
//
//   - "loud"    ⇒ clipping risk.  Triggered when peak > 0.9 (matches
//                 LiveQualityFeedback.tooLoud) OR level > 0.7 (the
//                 former LevelBar colour ladder's destructive band —
//                 now surfaced only via the ⚠ clipping icon + tier
//                 word in aria-valuetext, not the fill colour).
//   - "good"    ⇒ healthy signal.  Triggered when peak > 0.05 (matches
//                 LiveQualityFeedback.hasVoice) — once voice is detected
//                 we treat the level as healthy unless clipping.
//   - "silent"  ⇒ effectively no input.  level ≤ 0.005 (matches
//                 LiveQualityFeedback.volumeVeryLow when no voice).
//   - "low"     ⇒ faint signal — user should speak up.
//
// The FILL colour tracks a three-way band (normal / warning /
// clipping — see ``getFillColorTier`` below) with a smooth
// background-color crossfade, so the bar reads as a full-width meter
// with no reserved icon slot stealing track width.

export type VolumeTier = "silent" | "low" | "good" | "loud";

// ── Fill colour tiers ─────────────────────────────────────────────────
//
// The fill's BACKGROUND colour is binary — normal (primary blue) below
// the clipping onset, destructive red at/above it — so the bar reads
// as a true full-width meter with no reserved icon slot stealing track
// width. This is deliberately a SEPARATE concept from the qualitative
// ``VolumeTier`` above (silent/low/good/loud), which continues to
// drive the aria-valuetext announcement unchanged (so e.g. level 0.75
// still announces "loud" while painting blue — the announcement bands
// and the paint bands intentionally differ).

/** RMS level above which the fill turns destructive red. */
export const FILL_CLIPPING_LEVEL = 0.9;

/** Visual fill colour band. See the block comment above. */
export type FillColorTier = "normal" | "clipping";

export function getFillColorTier(level: number): FillColorTier {
	if (level > FILL_CLIPPING_LEVEL) return "clipping";
	return "normal";
}

/** Token-backed fill classes — ``bg-destructive`` resolves via the
 *  shared ``--destructive`` theme token. No hardcoded colour values
 *  anywhere. */
const FILL_COLOR_CLASS: Record<FillColorTier, string> = {
	normal: "bg-primary",
	clipping: "bg-destructive",
};

export function getVolumeTier(level: number, peak: number): VolumeTier {
	// Clipping — peak above 0.9 OR RMS above 0.7.  Either is a strong
	// signal that the input is saturating.
	if (peak > 0.9 || level > 0.7) return "loud";
	// Voice detected — treat as "good" regardless of RMS.  Matches
	// the original LiveQualityFeedback "excellent" branch which
	// fired on ``hasVoice && !tooLoud``.
	if (peak > 0.05) return "good";
	// Below the silence floor.
	if (level <= 0.005) return "silent";
	// Faint signal — encourage the user to speak up.
	return "low";
}

export function LevelBar({ level, playing }: LevelBarProps) {
	// LevelBar doesn't receive ``peak``; pass ``level`` as both args
	// so the centralised helper still classifies the clipping tier
	// correctly.
	const tier = getVolumeTier(level, level);
	// Visual colour band, derived from the numeric level only.
	const colorTier = getFillColorTier(level);
	// aria-valuetext gives SR users a qualitative reading (e.g.
	// "70 percent, loud") instead of just the raw number from
	// aria-valuenow. The tier word comes from the centralised
	// ``getVolumeTier`` classifier, so the announcement always conveys
	// the qualitative band. Without this, SR users would hear "70" with
	// no signal that the level is clipping.
	const pct = Math.round(level * 100);
	const ariaValueText = `${pct} percent, ${tier}`;
	return (
		<div
			className={cn(
				// Neutral track: no border — the fill is flush with the
				// track (same height, rounded ends), so an outline around
				// the bar read as a diluting extra layer. ``bg-input/30``
				// / ``bg-(--text-muted)/10`` (frozen) keep the empty track
				// visible in dark + light themes without any outline.
				// The track IS the component root: with the clipping icon
				// gone there is no sibling to lay out, so no wrapper is
				// needed and the bar spans the full available width.
				"h-1.5 w-full rounded-full overflow-hidden transition-opacity duration-200",
				playing ? "bg-(--text-muted)/10" : "bg-input/30",
			)}
			role="progressbar"
			aria-label={
				playing
					? t("microphone.levelBarFrozenAria")
					: t("microphone.levelBarAria")
			}
			aria-valuemin={0}
			aria-valuemax={100}
			aria-valuenow={pct}
			aria-valuetext={ariaValueText}
		>
			<div
				className={cn(
					// PERF: fixed-width fill animated via ``transform: scaleX()``
					// (compositor-friendly). ``mic_level`` events arrive at up
					// to ~10-30 Hz while recording; the previous animating
					// ``width`` percentage forced a layout pass on every tick,
					// while ``scaleX`` runs entirely on the compositor.
					// ``origin-left`` pins the fill to the track's left edge so
					// the rendered result is identical to the width-based bar.
					// Opacity rides along in the transition list because the
					// frozen ("playing") state fades the fill too. The fill
					// colour tracks ``colorTier`` (primary blue below 90%,
					// destructive red above); background-color crossfades on
					// its own quicker duration (120ms — fast enough to feel
					// snappy, slow enough to read as a fade rather than a
					// snap) than the transform/opacity smoothing (75ms) —
					// the arbitrary-property list maps 1:1 onto the
					// transition-property list order, so keep the two lists
					// in lockstep when editing.
					"h-full w-full origin-left transition-[transform,opacity,background-color] duration-[75ms,75ms,120ms]",
					FILL_COLOR_CLASS[colorTier],
					playing && "opacity-30",
				)}
				style={
					{
						// ``Math.max(1, …)`` previously pinned an empty bar to 1%
						// even when the user was totally silent — visually lying
						// that there's "some" signal. Use ``Math.max(0, …)`` on the
						// scale factor so a silent input renders a truly empty
						// track (``scaleX(0)`` collapses the fill completely).
						transform: `scaleX(${Math.max(0, level)})`,
						// ROUNDED CAPS UNDER scaleX: a transform scales painted
						// geometry, so a fixed radius compresses
						// horizontally (caps look squared at small levels).
						// Only the RIGHT (leading/moving) edge needs that
						// compensation — its horizontal radius is DIVIDED by
						// the scale so the POST-transform cap stays an exact
						// 3px semicircle at every value. The LEFT edge is
						// anchored (origin-left, never moves), so it takes a
						// plain fixed 3px matching the track's own corners;
						// feeding it the compensated formula instead leaves
						// a sliver of track background bleeding through the
						// anchored corners. The vertical radius stays a plain
						// 3px on all four corners (note the space-separated
						// horizontal/vertical form on the right — a single
						// value would wrongly scale the vertical axis too).
						// CSS's radius-overlap rule gracefully clamps the
						// huge computed radius to a capsule on tiny slivers,
						// and ``scaleX(0)`` hides the fill anyway (the 0.03
						// floor only guards the divide). ``--level`` comes
						// from the ~8 Hz throttled state sync (NOT the 60 Hz
						// rAF path — the rAF loop still writes ONLY the
						// transform), so this is a paint-only update on one
						// 6px strip, imperceptible next to the transform
						// motion.
						"--level": Math.max(0, level),
						borderTopLeftRadius: "3px",
						borderBottomLeftRadius: "3px",
						borderTopRightRadius: "calc(3px / max(var(--level), 0.03)) 3px",
						borderBottomRightRadius: "calc(3px / max(var(--level), 0.03)) 3px",
					} as CSSProperties
				}
			/>
		</div>
	);
}
