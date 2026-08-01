/**
 * Bubble overlay package — shared pure helpers.
 *
 * The two helpers here have no React state and no IPC side effects —
 * they are pure functions safe to unit-test in isolation.
 */
import { t } from "@/i18n/i18n";

/**
 * Translation-with-fallback helper. The i18n `t()` returns the raw key
 * string when the key is missing from every locale dictionary. We fall
 * back to a sensible English label instead of rendering the raw key.
 */
export function tf(key: string, fallback: string): string {
	const v = t(key);
	return v === key ? fallback : v;
}

/**
 * RMS → normalised level [0, 1]. Speech RMS typically lives in
 * [0, ~0.3]; we apply a soft compressor so loud transients don't peg
 * every bar. Multiplier 8 (was 5) so quiet speech visibly animates.
 */
export function rmsToNorm(rms: number): number {
	return Math.min(1, rms * 8);
}
