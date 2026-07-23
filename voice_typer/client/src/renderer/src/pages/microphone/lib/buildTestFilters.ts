// ADR 0007: Preset → filter mapping is owned by the backend
// (voice_typer/server/audio_presets.py). The Microphone page just sends
// the selected preset name to set_config; the backend applies the
// individual filter toggles. No client-side PRESET_TO_FILTERS table.
//
// ``buildTestFilters`` is the one exception: the test-recording path
// needs the *current* filter values (not just the preset name) so the
// captured audio can be run through the same chain the user has
// configured. The values are read from ``config`` and forwarded to
// ``microphone_test_start``.

import type { VoiceTyperConfig } from "@/types/config";

/**
 * Build the noise-filter dict sent to ``microphone_test_start`` so the
 * backend's ``level_monitor.stop_test_recording`` can run the captured
 * audio through the same chain the user has configured.
 *
 * When the preset is ``"off"`` (or no config is loaded yet) only the
 * master ``noise_filter_enabled: false`` flag is sent — the backend
 * then bypasses the entire filter chain for the test recording.
 */
export function buildTestFilters(
	config: VoiceTyperConfig | null,
): Record<string, unknown> {
	if (!config || config.audio_preset === "off") {
		return { noise_filter_enabled: false };
	}
	return {
		noise_filter_enabled: true,
		noise_filter_highpass: config.noise_filter_highpass ?? true,
		noise_filter_highpass_cutoff_hz:
			config.noise_filter_highpass_cutoff_hz ?? 80,
		noise_suppression_method: config.noise_suppression_method ?? "rnnoise",
		noise_filter_gate: config.noise_filter_gate ?? true,
		noise_filter_gate_open_threshold_db:
			config.noise_filter_gate_open_threshold_db ?? -26,
		noise_filter_gate_close_threshold_db:
			config.noise_filter_gate_close_threshold_db ?? -32,
		noise_filter_gate_attack_ms: config.noise_filter_gate_attack_ms ?? 25,
		noise_filter_gate_hold_ms: config.noise_filter_gate_hold_ms ?? 200,
		noise_filter_gate_release_ms: config.noise_filter_gate_release_ms ?? 150,
		noise_filter_eq: config.noise_filter_eq ?? true,
		noise_filter_eq_low_db: config.noise_filter_eq_low_db ?? -3,
		noise_filter_eq_mid_db: config.noise_filter_eq_mid_db ?? 3,
		noise_filter_eq_high_db: config.noise_filter_eq_high_db ?? 2,
		noise_filter_compressor: config.noise_filter_compressor ?? true,
		noise_filter_compressor_threshold_db:
			config.noise_filter_compressor_threshold_db ?? -18,
		noise_filter_compressor_ratio: config.noise_filter_compressor_ratio ?? 3,
		noise_filter_compressor_attack_ms:
			config.noise_filter_compressor_attack_ms ?? 6,
		noise_filter_compressor_release_ms:
			config.noise_filter_compressor_release_ms ?? 60,
		noise_filter_compressor_output_gain_db:
			config.noise_filter_compressor_output_gain_db ?? 0,
		noise_filter_limiter: config.noise_filter_limiter ?? true,
		noise_filter_limiter_ceiling_db:
			config.noise_filter_limiter_ceiling_db ?? -6,
		noise_filter_limiter_release_ms:
			config.noise_filter_limiter_release_ms ?? 60,
		noise_filter_notch: config.noise_filter_notch ?? false,
		noise_filter_notch_frequency_hz:
			config.noise_filter_notch_frequency_hz ?? 0,
	};
}
