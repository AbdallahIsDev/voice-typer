// Data-driven registry of every distinct SettingRow rendered by
// `<AudioFilterChain>` — the single source of truth for both the
// row's i18n keys AND its rendering metadata (config key, kind,
// min/max/step, aria/info keys, default value, optional parent
// toggle).
//
//previously this registry lived inside `AudioFilterChain.tsx`
// and held only `{ labelKey, infoSearchKey }`. The actual render
// metadata (config key, min/max/step, suffix, aria/info keys,
// default value, parent toggle) was duplicated inline across ~520 LOC
// of JSX. Adding a new filter row required touching 4 places: the
// registry, the labels useMemo, the destructure block, and the JSX
// body. Now the registry IS the render spec — the JSX is a single
// `.map` over `audioFilterRowDescriptors`.
//
// previously `AudioSettingsSection`'s `sectionItems` array
// (used for the section-level "any item visible?" check) listed only
// 16 of the 24 distinct filter rows. The 8 missing sub-rows
// (`gateAttack`, `gateHold`, `gateRelease`, `compressorAttack`,
// `compressorRelease`, `compressorOutputGain`, `limiterRelease`,
// `notchFrequency`) all have `*InfoSearch` keys in `en.json` but were
// not in the array, so searching for one of those sub-row labels
// caused the entire Audio Enhancement section to be hidden.
//
// Co-locating the registry here (rather than duplicating the list in
// `AudioSettingsSection`) keeps it next to the component that
// actually renders the rows — any future filter row added to the
// registry is automatically picked up by the section's search
// visibility check.

import type { VoiceTyperConfig } from "@/types/config";

/**
 * Single shared i18n key for the Audio Enhancement section title
 * (used by every descriptor's `sectionTitleKey`). Extracted as a
 * constant so callers (e.g. `AudioSettingsSection`'s visibility
 * check) can reference it without re-typing the string.
 */
export const AUDIO_SECTION_TITLE_KEY = "settings.audioEnhancement.title";

/**
 * Option for a `kind: "select"` row. Either `label` (a plain
 * non-i18n string like "RNNoise") or `labelKey` (an i18n key like
 * `"settings.audioEnhancement.noneOption"`) — mutually exclusive.
 */
export interface AudioFilterSelectOption {
	value: string;
	/** Plain (non-i18n) label. Mutually exclusive with `labelKey`. */
	label?: string;
	/** i18n key for the option's label. Mutually exclusive with `label`. */
	labelKey?: string;
}

export interface AudioFilterRowDescriptor {
	/** i18n key for the row's visible label, e.g. `"settings.audioEnhancement.gateAttack"`. */
	labelKey: string;
	/** i18n key for the row's search-visible info, e.g. `"settings.audioEnhancement.gateAttackInfoSearch"`. */
	infoSearchKey: string;
	/** i18n key for the section title (used in the search visibility predicate). */
	sectionTitleKey: string;
	/**
	 * Config key this row reads/writes, e.g. `"noise_filter_highpass"`.
	 * Typed as `keyof VoiceTyperConfig` so renames surface at compile time.
	 */
	configKey: keyof VoiceTyperConfig;
	/** Render kind: toggle (Switch), slider (RangeSlider), or select (Select). */
	kind: "toggle" | "slider" | "select";
	/** i18n key for the `SettingRow`'s info tooltip (the `t("...Info")` call). */
	infoKey: string;
	/** i18n key for the control's `aria-label` (the `t("...Aria")` call). */
	ariaKey: string;
	/**
	 * Fallback value when `config[configKey]` is `undefined`.
	 * `boolean` for toggles, `number` for sliders, `string` for selects.
	 */
	defaultValue: number | boolean | string;
	/** Slider-only: minimum value. */
	min?: number;
	/** Slider-only: maximum value. */
	max?: number;
	/** Slider-only: step size. */
	step?: number;
	/** Slider-only: unit suffix (e.g. `"Hz"`, `"dB"`, `"ms"`, `":1"`). */
	suffix?: string;
	/**
	 * When set, this row only renders when `config[parentToggle]` is
	 * truthy (after applying the parent's own `defaultValue` fallback).
	 * Used for sub-rows like high-pass cutoff (under
	 * `noise_filter_highpass`). The parent toggle's `defaultValue` is
	 * looked up from the descriptor with this `configKey`.
	 */
	parentToggle?: keyof VoiceTyperConfig;
	/** Select-only: option list. */
	options?: readonly AudioFilterSelectOption[];
}

/**
 * Static registry of every distinct SettingRow rendered by
 * `<AudioFilterChain>`. The order is the render order.
 */
export const audioFilterRowDescriptors: readonly AudioFilterRowDescriptor[] = [
	// ─── High-pass filter ──────────────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.highPassFilter",
		infoSearchKey: "settings.audioEnhancement.highPassFilterInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_highpass",
		kind: "toggle",
		infoKey: "settings.audioEnhancement.highPassFilterInfo",
		ariaKey: "settings.audioEnhancement.highPassFilterAria",
		defaultValue: true,
	},
	{
		labelKey: "settings.audioEnhancement.highPassCutoff",
		infoSearchKey: "settings.audioEnhancement.highPassCutoffInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_highpass_cutoff_hz",
		kind: "slider",
		infoKey: "settings.audioEnhancement.highPassCutoffInfo",
		ariaKey: "settings.audioEnhancement.highPassCutoffAria",
		defaultValue: 80,
		min: 20,
		max: 500,
		step: 10,
		suffix: "Hz",
		parentToggle: "noise_filter_highpass",
	},

	// ─── Noise suppression method ──────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.noiseSuppression",
		infoSearchKey: "settings.audioEnhancement.noiseSuppressionInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_suppression_method",
		kind: "select",
		infoKey: "settings.audioEnhancement.noiseSuppressionInfo",
		ariaKey: "settings.audioEnhancement.noiseSuppressionAria",
		defaultValue: "rnnoise",
		options: [
			{ value: "rnnoise", label: "RNNoise" },
			{ value: "deepfilternet", label: "DeepFilterNet" },
			// Fix 7: Speex was documented in the info tooltip but was
			// missing from the dropdown — selecting it required hand-
			// editing config.json. Now it's a first-class option.
			{ value: "speex", label: "Speex" },
			{ value: "none", labelKey: "settings.audioEnhancement.noneOption" },
		],
	},

	// ─── Noise gate ────────────────────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.noiseGate",
		infoSearchKey: "settings.audioEnhancement.noiseGateInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_gate",
		kind: "toggle",
		infoKey: "settings.audioEnhancement.noiseGateInfo",
		ariaKey: "settings.audioEnhancement.noiseGateAria",
		defaultValue: true,
	},
	{
		labelKey: "settings.audioEnhancement.gateOpenThreshold",
		infoSearchKey: "settings.audioEnhancement.gateOpenThresholdInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_gate_open_threshold_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.gateOpenThresholdInfo",
		ariaKey: "settings.audioEnhancement.gateOpenThresholdAria",
		defaultValue: -26,
		min: -96,
		max: 0,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_gate",
	},
	{
		labelKey: "settings.audioEnhancement.gateCloseThreshold",
		infoSearchKey: "settings.audioEnhancement.gateCloseThresholdInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_gate_close_threshold_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.gateCloseThresholdInfo",
		ariaKey: "settings.audioEnhancement.gateCloseThresholdAria",
		defaultValue: -32,
		min: -96,
		max: 0,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_gate",
	},
	{
		labelKey: "settings.audioEnhancement.gateAttack",
		infoSearchKey: "settings.audioEnhancement.gateAttackInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_gate_attack_ms",
		kind: "slider",
		infoKey: "settings.audioEnhancement.gateAttackInfo",
		ariaKey: "settings.audioEnhancement.gateAttackAria",
		defaultValue: 25,
		min: 0,
		max: 200,
		step: 1,
		suffix: "ms",
		parentToggle: "noise_filter_gate",
	},
	{
		labelKey: "settings.audioEnhancement.gateHold",
		infoSearchKey: "settings.audioEnhancement.gateHoldInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_gate_hold_ms",
		kind: "slider",
		infoKey: "settings.audioEnhancement.gateHoldInfo",
		ariaKey: "settings.audioEnhancement.gateHoldAria",
		defaultValue: 200,
		min: 0,
		max: 1000,
		step: 10,
		suffix: "ms",
		parentToggle: "noise_filter_gate",
	},
	{
		labelKey: "settings.audioEnhancement.gateRelease",
		infoSearchKey: "settings.audioEnhancement.gateReleaseInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_gate_release_ms",
		kind: "slider",
		infoKey: "settings.audioEnhancement.gateReleaseInfo",
		ariaKey: "settings.audioEnhancement.gateReleaseAria",
		defaultValue: 150,
		min: 0,
		max: 1000,
		step: 5,
		suffix: "ms",
		parentToggle: "noise_filter_gate",
	},

	// ─── Equalizer ─────────────────────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.equalizer",
		infoSearchKey: "settings.audioEnhancement.equalizerInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_eq",
		kind: "toggle",
		infoKey: "settings.audioEnhancement.equalizerInfo",
		ariaKey: "settings.audioEnhancement.equalizerAria",
		defaultValue: true,
	},
	{
		labelKey: "settings.audioEnhancement.eqLow",
		infoSearchKey: "settings.audioEnhancement.eqLowInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_eq_low_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.eqLowInfo",
		ariaKey: "settings.audioEnhancement.eqLowAria",
		defaultValue: -3,
		min: -20,
		max: 20,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_eq",
	},
	{
		labelKey: "settings.audioEnhancement.eqMid",
		infoSearchKey: "settings.audioEnhancement.eqMidInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_eq_mid_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.eqMidInfo",
		ariaKey: "settings.audioEnhancement.eqMidAria",
		defaultValue: 3,
		min: -20,
		max: 20,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_eq",
	},
	{
		labelKey: "settings.audioEnhancement.eqHigh",
		infoSearchKey: "settings.audioEnhancement.eqHighInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_eq_high_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.eqHighInfo",
		ariaKey: "settings.audioEnhancement.eqHighAria",
		defaultValue: 2,
		min: -20,
		max: 20,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_eq",
	},

	// ─── Compressor ────────────────────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.compressor",
		infoSearchKey: "settings.audioEnhancement.compressorInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_compressor",
		kind: "toggle",
		infoKey: "settings.audioEnhancement.compressorInfo",
		ariaKey: "settings.audioEnhancement.compressorAria",
		defaultValue: true,
	},
	{
		labelKey: "settings.audioEnhancement.compressorThreshold",
		infoSearchKey: "settings.audioEnhancement.compressorThresholdInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_compressor_threshold_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.compressorThresholdInfo",
		ariaKey: "settings.audioEnhancement.compressorThresholdAria",
		defaultValue: -18,
		min: -60,
		max: 0,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_compressor",
	},
	{
		labelKey: "settings.audioEnhancement.compressorRatio",
		infoSearchKey: "settings.audioEnhancement.compressorRatioInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_compressor_ratio",
		kind: "slider",
		infoKey: "settings.audioEnhancement.compressorRatioInfo",
		ariaKey: "settings.audioEnhancement.compressorRatioAria",
		defaultValue: 3,
		min: 1,
		max: 32,
		step: 0.5,
		suffix: ":1",
		parentToggle: "noise_filter_compressor",
	},
	{
		labelKey: "settings.audioEnhancement.compressorAttack",
		infoSearchKey: "settings.audioEnhancement.compressorAttackInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_compressor_attack_ms",
		kind: "slider",
		infoKey: "settings.audioEnhancement.compressorAttackInfo",
		ariaKey: "settings.audioEnhancement.compressorAttackAria",
		defaultValue: 6,
		min: 0,
		max: 200,
		step: 1,
		suffix: "ms",
		parentToggle: "noise_filter_compressor",
	},
	{
		labelKey: "settings.audioEnhancement.compressorRelease",
		infoSearchKey: "settings.audioEnhancement.compressorReleaseInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_compressor_release_ms",
		kind: "slider",
		infoKey: "settings.audioEnhancement.compressorReleaseInfo",
		ariaKey: "settings.audioEnhancement.compressorReleaseAria",
		defaultValue: 60,
		min: 0,
		max: 1000,
		step: 5,
		suffix: "ms",
		parentToggle: "noise_filter_compressor",
	},
	{
		labelKey: "settings.audioEnhancement.compressorOutputGain",
		infoSearchKey: "settings.audioEnhancement.compressorOutputGainInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_compressor_output_gain_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.compressorOutputGainInfo",
		ariaKey: "settings.audioEnhancement.compressorOutputGainAria",
		defaultValue: 0,
		min: -24,
		max: 24,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_compressor",
	},

	// ─── Limiter ───────────────────────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.limiter",
		infoSearchKey: "settings.audioEnhancement.limiterInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_limiter",
		kind: "toggle",
		infoKey: "settings.audioEnhancement.limiterInfo",
		ariaKey: "settings.audioEnhancement.limiterAria",
		defaultValue: true,
	},
	{
		labelKey: "settings.audioEnhancement.limiterCeiling",
		infoSearchKey: "settings.audioEnhancement.limiterCeilingInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_limiter_ceiling_db",
		kind: "slider",
		infoKey: "settings.audioEnhancement.limiterCeilingInfo",
		ariaKey: "settings.audioEnhancement.limiterCeilingAria",
		defaultValue: -6,
		min: -60,
		max: 0,
		step: 1,
		suffix: "dB",
		parentToggle: "noise_filter_limiter",
	},
	{
		labelKey: "settings.audioEnhancement.limiterRelease",
		infoSearchKey: "settings.audioEnhancement.limiterReleaseInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_limiter_release_ms",
		kind: "slider",
		infoKey: "settings.audioEnhancement.limiterReleaseInfo",
		ariaKey: "settings.audioEnhancement.limiterReleaseAria",
		defaultValue: 60,
		min: 0,
		max: 1000,
		step: 5,
		suffix: "ms",
		parentToggle: "noise_filter_limiter",
	},

	// ─── Notch filter ──────────────────────────────────────────────
	{
		labelKey: "settings.audioEnhancement.notchFilter",
		infoSearchKey: "settings.audioEnhancement.notchFilterInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_notch",
		kind: "toggle",
		infoKey: "settings.audioEnhancement.notchFilterInfo",
		ariaKey: "settings.audioEnhancement.notchFilterAria",
		defaultValue: false,
	},
	{
		labelKey: "settings.audioEnhancement.notchFrequency",
		infoSearchKey: "settings.audioEnhancement.notchFrequencyInfoSearch",
		sectionTitleKey: AUDIO_SECTION_TITLE_KEY,
		configKey: "noise_filter_notch_frequency_hz",
		kind: "slider",
		infoKey: "settings.audioEnhancement.notchFrequencyInfo",
		ariaKey: "settings.audioEnhancement.notchFrequencyAria",
		defaultValue: 60,
		min: 50,
		max: 1000,
		step: 1,
		suffix: "Hz",
		parentToggle: "noise_filter_notch",
	},
];

/**
 * Lookup map from `configKey` → descriptor, used to resolve a
 * `parentToggle`'s `defaultValue` without scanning the array on
 * every render.
 */
export const audioFilterDescriptorByConfigKey: ReadonlyMap<
	string,
	AudioFilterRowDescriptor
> = new Map(audioFilterRowDescriptors.map((d) => [d.configKey as string, d]));
