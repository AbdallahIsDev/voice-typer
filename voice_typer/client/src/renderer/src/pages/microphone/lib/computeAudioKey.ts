import type { VoiceTyperConfig } from "@/types/config";

/**
 * Compute a stable string key from the audio-related config fields so
 * the page can detect "filters changed since last test" and prompt the
 * user to re-run the test.
 *
 * The key is a JSON-serialised subset of ``config`` — every field that
 * influences the post-test filter chain. Compared against the snapshot
 * taken at ``startTest`` time (``filtersSinceLastTest``) to decide
 * whether to show the "filters changed — retest" notice.
 */
export function computeAudioKey(config: VoiceTyperConfig | null): string {
	if (!config) return "";
	return JSON.stringify({
		preset: config.audio_preset,
		hp: config.noise_filter_highpass,
		hp_cut: config.noise_filter_highpass_cutoff_hz,
		method: config.noise_suppression_method,
		gate: config.noise_filter_gate,
		gate_open: config.noise_filter_gate_open_threshold_db,
		gate_close: config.noise_filter_gate_close_threshold_db,
		gate_attack: config.noise_filter_gate_attack_ms,
		gate_hold: config.noise_filter_gate_hold_ms,
		gate_release: config.noise_filter_gate_release_ms,
		eq: config.noise_filter_eq,
		eq_low: config.noise_filter_eq_low_db,
		eq_mid: config.noise_filter_eq_mid_db,
		eq_high: config.noise_filter_eq_high_db,
		comp: config.noise_filter_compressor,
		comp_thr: config.noise_filter_compressor_threshold_db,
		comp_ratio: config.noise_filter_compressor_ratio,
		comp_attack: config.noise_filter_compressor_attack_ms,
		comp_release: config.noise_filter_compressor_release_ms,
		comp_out: config.noise_filter_compressor_output_gain_db,
		lim: config.noise_filter_limiter,
		lim_ceil: config.noise_filter_limiter_ceiling_db,
		lim_rel: config.noise_filter_limiter_release_ms,
		notch: config.noise_filter_notch,
		notch_freq: config.noise_filter_notch_frequency_hz,
	});
}
