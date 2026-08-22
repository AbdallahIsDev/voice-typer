import { Alert02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
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
//                 LiveQualityFeedback.tooLoud) OR level > 0.7 (matches
//                 LevelBar.getLevelColor's destructive band).
//   - "good"    ⇒ healthy signal.  Triggered when peak > 0.05 (matches
//                 LiveQualityFeedback.hasVoice) — once voice is detected
//                 we treat the level as healthy unless clipping.
//   - "silent"  ⇒ effectively no input.  level ≤ 0.005 (matches
//                 LiveQualityFeedback.volumeVeryLow when no voice).
//   - "low"     ⇒ faint signal — user should speak up.
//
// ``LevelBar`` doesn't track ``peak`` (it only receives RMS), so it
// passes ``level`` as both arguments.  The function is symmetric in
// that case: ``level > 0.7`` ⇒ "loud", ``level > 0.05`` ⇒ "good",
// ``level ≤ 0.005`` ⇒ "silent", else "low" — which preserves the
// original three-band colour ladder exactly.

export type VolumeTier = "silent" | "low" | "good" | "loud";

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

function getLevelColor(lvl: number): string {
	if (lvl > 0.7) return "var(--destructive)";
	if (lvl > 0.3) return "var(--primary)";
	return "var(--accent)";
}

export function LevelBar({ level, playing }: LevelBarProps) {
	// LevelBar doesn't receive ``peak``; pass ``level`` as both args
	// so the centralised helper still classifies the clipping tier
	// correctly (its ``level > 0.7`` band matches our red threshold).
	const tier = getVolumeTier(level, level);
	const clipping = tier === "loud";
	// aria-valuetext gives SR users a qualitative reading (e.g.
	// "70 percent, loud") instead of just the raw number from
	// aria-valuenow. The tier word comes from the centralised
	// ``getVolumeTier`` classifier so the announcement always
	// matches the on-screen colour band. Without this, SR users
	// would hear "70" with no signal that the level is clipping.
	const pct = Math.round(level * 100);
	const ariaValueText = `${pct} percent, ${tier}`;
	return (
		<div className="flex items-center gap-1">
			<div
				className={cn(
					// add a subtle border to the track so it stays
					// visible on light / low-contrast backgrounds even when
					// the level is zero (previously the track vanished on
					// pure-white surfaces).
					"h-1.5 w-full rounded-full border border-border/60 overflow-hidden transition-opacity duration-200",
					playing ? "bg-(--text-muted)/10" : "bg-border",
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
						// frozen ("playing") state fades the fill too.
						"h-full w-full origin-left rounded-full transition-[transform,opacity] duration-75",
						playing && "opacity-30",
					)}
					style={{
						// ``Math.max(1, …)`` previously pinned an empty bar to 1%
						// even when the user was totally silent — visually lying
						// that there's "some" signal. Use ``Math.max(0, …)`` on the
						// scale factor so a silent input renders a truly empty
						// track (``scaleX(0)`` collapses the fill completely).
						transform: `scaleX(${Math.max(0, level)})`,
						backgroundColor: getLevelColor(level),
					}}
				/>
			</div>
			{/* a colour-only "clipping" signal is invisible to
                            red-green colour-blind users (deuteranopia / protanopia
                            make the destructive red and primary blue look nearly
                            identical at small heights).  Render a ⚠ glyph next to
                            the bar whenever the tier is "loud" so the warning has
                            a non-colour channel too.  The icon is aria-hidden
                            because the progressbar's aria-valuenow already conveys
                            the numeric level; the warning text is announced by
                            LiveQualityFeedback's live region. */}
			{clipping && (
				<HugeiconsIcon
					icon={Alert02Icon}
					strokeWidth={2}
					className="h-3.5 w-3.5 shrink-0 text-destructive"
					aria-hidden="true"
				/>
			)}
		</div>
	);
}
