// AudioFilterChain — the shared individual-filter UI used by both
// `Settings → Audio` (custom preset) and the Microphone test page's
// collapsible preset selector.
//
// F-1: previously the two call sites each rendered their own copy of
// the high-pass / noise-suppression / noise-gate / EQ / compressor /
// limiter / notch rows, with subtly different wrappers (SettingRow in
// the Settings page vs custom ToggleRow/SliderRow in the microphone
// page) and the microphone page lagging on i18n. This component
// consolidates them into one canonical implementation that uses the
// shared `SettingRow` + `RangeSlider` primitives and the shared
// `t()` translation keys.
//
// Both call sites pass the same `config` (the full VoiceTyperConfig)
// and an `onConfigChange` callback that receives a partial update.
// The component is purely presentational — it does not mutate config
// directly.
//
// the optional `isVisible` prop wires the per-row
// search filter that the Settings page already uses for its own
// SettingRows. When the user types a query into the Settings search
// box, the parent Settings page hands `isVisible` down through
// AudioSettingsSection → AudioFilterChain, and each SettingRow here
// is wrapped in `filterIsVisible(label, infoSearch, audioSectionTitle)
// && …` so a query like "compressor ratio" surfaces ONLY the matching
// row instead of the entire custom filter chain. Non-Settings call
// sites (e.g. AudioPresetSelector on the Microphone test page) omit
// the prop and fall back to a permissive `() => true`, preserving
// their existing behaviour.

import { RangeSlider } from "@/components/common/RangeSlider";
import { SettingRow } from "@/components/common/SettingRow";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { t } from "@/i18n/i18n";
import type { VoiceTyperConfig } from "@/types/config";

export interface AudioFilterChainProps {
	/** Full config — used to read the individual noise_filter_* fields. */
	config: VoiceTyperConfig;
	/**
	 * Called when any individual filter field changes. Receives a
	 * Partial<VoiceTyperConfig> (same shape as `updateConfig` in the
	 * Settings page and `onConfigChange` in the Microphone page).
	 */
	onConfigChange: (updates: Partial<VoiceTyperConfig>) => void;
	/**
	 * Optional search-filter predicate. Returns true when the row
	 * should be shown. Defaults to a permissive `() => true` so
	 * non-Settings call sites (e.g. AudioPresetSelector on the
	 * Microphone page) are unaffected — they render the entire
	 * custom chain unconditionally.
	 *
	 * this wires the per-row search filter that was
	 * previously documented in AudioSettingsSection's comment but
	 * never actually implemented.
	 */
	isVisible?: (label: string, info: string, sectionTitle: string) => boolean;
}

/**
 * Static registry of every distinct SettingRow rendered by
 * `<AudioFilterChain>` — label key + the search-visible info key.
 *
 * previously `AudioSettingsSection`'s `sectionItems` array
 * (used for the section-level "any item visible?" check) listed only
 * 16 of the 24 distinct filter rows. The 8 missing sub-rows
 * (`gateAttack`, `gateHold`, `gateRelease`, `compressorAttack`,
 * `compressorRelease`, `compressorOutputGain`, `limiterRelease`,
 * `notchFrequency`) all have `*InfoSearch` keys in `en.json` but were
 * not in the array, so searching for one of those sub-row labels
 * caused the entire Audio Enhancement section to be hidden.
 *
 * Co-locating the registry here (rather than duplicating the list in
 * `AudioSettingsSection`) keeps it next to the component that
 * actually renders the rows — any future filter row added to
 * `<AudioFilterChain>` is automatically picked up by the section's
 * search visibility check.
 */
export interface AudioFilterRowDescriptor {
	/** i18n key for the row's visible label, e.g. `"settings.audioEnhancement.gateAttack"`. */
	labelKey: string;
	/** i18n key for the row's search-visible info, e.g. `"settings.audioEnhancement.gateAttackInfoSearch"`. */
	infoSearchKey: string;
}

export const audioFilterRowDescriptors: readonly AudioFilterRowDescriptor[] = [
	{
		labelKey: "settings.audioEnhancement.highPassFilter",
		infoSearchKey: "settings.audioEnhancement.highPassFilterInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.highPassCutoff",
		infoSearchKey: "settings.audioEnhancement.highPassCutoffInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.noiseSuppression",
		infoSearchKey: "settings.audioEnhancement.noiseSuppressionInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.noiseGate",
		infoSearchKey: "settings.audioEnhancement.noiseGateInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.gateOpenThreshold",
		infoSearchKey: "settings.audioEnhancement.gateOpenThresholdInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.gateCloseThreshold",
		infoSearchKey: "settings.audioEnhancement.gateCloseThresholdInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.gateAttack",
		infoSearchKey: "settings.audioEnhancement.gateAttackInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.gateHold",
		infoSearchKey: "settings.audioEnhancement.gateHoldInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.gateRelease",
		infoSearchKey: "settings.audioEnhancement.gateReleaseInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.equalizer",
		infoSearchKey: "settings.audioEnhancement.equalizerInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.eqLow",
		infoSearchKey: "settings.audioEnhancement.eqLowInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.eqMid",
		infoSearchKey: "settings.audioEnhancement.eqMidInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.eqHigh",
		infoSearchKey: "settings.audioEnhancement.eqHighInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.compressor",
		infoSearchKey: "settings.audioEnhancement.compressorInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.compressorThreshold",
		infoSearchKey: "settings.audioEnhancement.compressorThresholdInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.compressorRatio",
		infoSearchKey: "settings.audioEnhancement.compressorRatioInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.compressorAttack",
		infoSearchKey: "settings.audioEnhancement.compressorAttackInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.compressorRelease",
		infoSearchKey: "settings.audioEnhancement.compressorReleaseInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.compressorOutputGain",
		infoSearchKey: "settings.audioEnhancement.compressorOutputGainInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.limiter",
		infoSearchKey: "settings.audioEnhancement.limiterInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.limiterCeiling",
		infoSearchKey: "settings.audioEnhancement.limiterCeilingInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.limiterRelease",
		infoSearchKey: "settings.audioEnhancement.limiterReleaseInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.notchFilter",
		infoSearchKey: "settings.audioEnhancement.notchFilterInfoSearch",
	},
	{
		labelKey: "settings.audioEnhancement.notchFrequency",
		infoSearchKey: "settings.audioEnhancement.notchFrequencyInfoSearch",
	},
];

/**
 * Renders the custom filter chain: high-pass, noise suppression
 * method, noise gate (with open/close thresholds + attack/hold/release),
 * equalizer (with low/mid/high), compressor (with threshold/ratio/
 * attack/release/output_gain), limiter (with ceiling + release), and
 * notch filter (with frequency).
 *
 * Each row uses `SettingRow` for layout consistency with the rest of
 * the Settings page. Sliders use `RangeSlider` for the same reason.
 *
 * PVT-037 / Fix 11: every RangeSlider uses `deferApply` so a drag does
 * not flood the backend with one `set_config` IPC call per pixel — the
 * commit happens on pointer-up / blur / key-up instead.
 *
 * All labels are translated via `t()` from `@/i18n/i18n` — the keys
 * live under `settings.audioEnhancement.*` and are shared with the
 * Settings page.
 *
 * every SettingRow is wrapped in an `isVisible` check so the
 * Settings search box can surface individual filter sub-rows.
 *
 * the 25 inline per-field handler definitions were collapsed
 * into a single generic `set(k, v)` helper. Behaviour is byte-level
 * identical — the same `{ [k]: v }` partial is sent through
 * `onConfigChange`.
 */
export function AudioFilterChain({
	config,
	onConfigChange,
	isVisible,
}: AudioFilterChainProps) {
	// permissive default so non-Settings call sites (the
	// Microphone test page's AudioPresetSelector) keep rendering
	// every row unconditionally.
	const filterIsVisible = isVisible ?? (() => true);
	const audioSectionTitle = t("settings.audioEnhancement.title");

	// a single generic helper replaces 25 per-field inline
	// handlers. The generic `K` ensures the key/value pair stays
	// type-checked against `VoiceTyperConfig`.
	const set = <K extends keyof VoiceTyperConfig>(
		k: K,
		v: VoiceTyperConfig[K],
	) => onConfigChange({ [k]: v } as Partial<VoiceTyperConfig>);

	// resolve the translated search-visible labels once per
	// render so the per-row `filterIsVisible` check and the SettingRow
	// `label` prop share the same strings (mirrors the pattern in
	// AudioSettingsSection).
	const highPassFilterLabel = t("settings.audioEnhancement.highPassFilter");
	const highPassFilterInfoSearch = t(
		"settings.audioEnhancement.highPassFilterInfoSearch",
	);
	const highPassCutoffLabel = t("settings.audioEnhancement.highPassCutoff");
	const highPassCutoffInfoSearch = t(
		"settings.audioEnhancement.highPassCutoffInfoSearch",
	);
	const noiseSuppressionLabel = t("settings.audioEnhancement.noiseSuppression");
	const noiseSuppressionInfoSearch = t(
		"settings.audioEnhancement.noiseSuppressionInfoSearch",
	);
	const noiseGateLabel = t("settings.audioEnhancement.noiseGate");
	const noiseGateInfoSearch = t(
		"settings.audioEnhancement.noiseGateInfoSearch",
	);
	const gateOpenThresholdLabel = t(
		"settings.audioEnhancement.gateOpenThreshold",
	);
	const gateOpenThresholdInfoSearch = t(
		"settings.audioEnhancement.gateOpenThresholdInfoSearch",
	);
	const gateCloseThresholdLabel = t(
		"settings.audioEnhancement.gateCloseThreshold",
	);
	const gateCloseThresholdInfoSearch = t(
		"settings.audioEnhancement.gateCloseThresholdInfoSearch",
	);
	const gateAttackLabel = t("settings.audioEnhancement.gateAttack");
	const gateAttackInfoSearch = t(
		"settings.audioEnhancement.gateAttackInfoSearch",
	);
	const gateHoldLabel = t("settings.audioEnhancement.gateHold");
	const gateHoldInfoSearch = t("settings.audioEnhancement.gateHoldInfoSearch");
	const gateReleaseLabel = t("settings.audioEnhancement.gateRelease");
	const gateReleaseInfoSearch = t(
		"settings.audioEnhancement.gateReleaseInfoSearch",
	);
	const equalizerLabel = t("settings.audioEnhancement.equalizer");
	const equalizerInfoSearch = t(
		"settings.audioEnhancement.equalizerInfoSearch",
	);
	const eqLowLabel = t("settings.audioEnhancement.eqLow");
	const eqLowInfoSearch = t("settings.audioEnhancement.eqLowInfoSearch");
	const eqMidLabel = t("settings.audioEnhancement.eqMid");
	const eqMidInfoSearch = t("settings.audioEnhancement.eqMidInfoSearch");
	const eqHighLabel = t("settings.audioEnhancement.eqHigh");
	const eqHighInfoSearch = t("settings.audioEnhancement.eqHighInfoSearch");
	const compressorLabel = t("settings.audioEnhancement.compressor");
	const compressorInfoSearch = t(
		"settings.audioEnhancement.compressorInfoSearch",
	);
	const compressorThresholdLabel = t(
		"settings.audioEnhancement.compressorThreshold",
	);
	const compressorThresholdInfoSearch = t(
		"settings.audioEnhancement.compressorThresholdInfoSearch",
	);
	const compressorRatioLabel = t("settings.audioEnhancement.compressorRatio");
	const compressorRatioInfoSearch = t(
		"settings.audioEnhancement.compressorRatioInfoSearch",
	);
	const compressorAttackLabel = t("settings.audioEnhancement.compressorAttack");
	const compressorAttackInfoSearch = t(
		"settings.audioEnhancement.compressorAttackInfoSearch",
	);
	const compressorReleaseLabel = t(
		"settings.audioEnhancement.compressorRelease",
	);
	const compressorReleaseInfoSearch = t(
		"settings.audioEnhancement.compressorReleaseInfoSearch",
	);
	const compressorOutputGainLabel = t(
		"settings.audioEnhancement.compressorOutputGain",
	);
	const compressorOutputGainInfoSearch = t(
		"settings.audioEnhancement.compressorOutputGainInfoSearch",
	);
	const limiterLabel = t("settings.audioEnhancement.limiter");
	const limiterInfoSearch = t("settings.audioEnhancement.limiterInfoSearch");
	const limiterCeilingLabel = t("settings.audioEnhancement.limiterCeiling");
	const limiterCeilingInfoSearch = t(
		"settings.audioEnhancement.limiterCeilingInfoSearch",
	);
	const limiterReleaseLabel = t("settings.audioEnhancement.limiterRelease");
	const limiterReleaseInfoSearch = t(
		"settings.audioEnhancement.limiterReleaseInfoSearch",
	);
	const notchFilterLabel = t("settings.audioEnhancement.notchFilter");
	const notchFilterInfoSearch = t(
		"settings.audioEnhancement.notchFilterInfoSearch",
	);
	const notchFrequencyLabel = t("settings.audioEnhancement.notchFrequency");
	const notchFrequencyInfoSearch = t(
		"settings.audioEnhancement.notchFrequencyInfoSearch",
	);

	return (
		<>
			{/* High-pass filter */}
			{filterIsVisible(
				highPassFilterLabel,
				highPassFilterInfoSearch,
				audioSectionTitle,
			) && (
				<SettingRow
					label={highPassFilterLabel}
					info={t("settings.audioEnhancement.highPassFilterInfo")}
				>
					<Switch
						checked={config.noise_filter_highpass ?? true}
						onCheckedChange={(v) => set("noise_filter_highpass", v)}
						aria-label={t("settings.audioEnhancement.highPassFilterAria")}
					/>
				</SettingRow>
			)}
			{(config.noise_filter_highpass ?? true) &&
				filterIsVisible(
					highPassCutoffLabel,
					highPassCutoffInfoSearch,
					audioSectionTitle,
				) && (
					<SettingRow
						label={highPassCutoffLabel}
						info={t("settings.audioEnhancement.highPassCutoffInfo")}
					>
						<RangeSlider
							value={config.noise_filter_highpass_cutoff_hz ?? 80}
							min={20}
							max={500}
							step={10}
							onChange={(v) => set("noise_filter_highpass_cutoff_hz", v)}
							ariaLabel={t("settings.audioEnhancement.highPassCutoffAria")}
							suffix="Hz"
							deferApply
						/>
					</SettingRow>
				)}

			{/* Noise suppression method */}
			{filterIsVisible(
				noiseSuppressionLabel,
				noiseSuppressionInfoSearch,
				audioSectionTitle,
			) && (
				<SettingRow
					label={noiseSuppressionLabel}
					info={t("settings.audioEnhancement.noiseSuppressionInfo")}
				>
					<Select
						value={config.noise_suppression_method ?? "rnnoise"}
						onValueChange={(v) =>
							set(
								"noise_suppression_method",
								v as VoiceTyperConfig["noise_suppression_method"],
							)
						}
					>
						<SelectTrigger
							className="w-40"
							aria-label={t("settings.audioEnhancement.noiseSuppressionAria")}
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="rnnoise">RNNoise</SelectItem>
							<SelectItem value="deepfilternet">DeepFilterNet</SelectItem>
							{/* Fix 7: Speex was documented in the info tooltip but was
							    missing from the dropdown — selecting it required hand-
							    editing config.json. Now it's a first-class option. */}
							<SelectItem value="speex">Speex</SelectItem>
							<SelectItem value="none">
								{t("settings.audioEnhancement.noneOption")}
							</SelectItem>
						</SelectContent>
					</Select>
				</SettingRow>
			)}

			{/* Noise gate */}
			{filterIsVisible(
				noiseGateLabel,
				noiseGateInfoSearch,
				audioSectionTitle,
			) && (
				<SettingRow
					label={noiseGateLabel}
					info={t("settings.audioEnhancement.noiseGateInfo")}
				>
					<Switch
						checked={config.noise_filter_gate ?? true}
						onCheckedChange={(v) => set("noise_filter_gate", v)}
						aria-label={t("settings.audioEnhancement.noiseGateAria")}
					/>
				</SettingRow>
			)}
			{(config.noise_filter_gate ?? true) && (
				<>
					{filterIsVisible(
						gateOpenThresholdLabel,
						gateOpenThresholdInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={gateOpenThresholdLabel}
							info={t("settings.audioEnhancement.gateOpenThresholdInfo")}
						>
							<RangeSlider
								value={config.noise_filter_gate_open_threshold_db ?? -26}
								min={-96}
								max={0}
								step={1}
								onChange={(v) => set("noise_filter_gate_open_threshold_db", v)}
								ariaLabel={t("settings.audioEnhancement.gateOpenThresholdAria")}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						gateCloseThresholdLabel,
						gateCloseThresholdInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={gateCloseThresholdLabel}
							info={t("settings.audioEnhancement.gateCloseThresholdInfo")}
						>
							<RangeSlider
								value={config.noise_filter_gate_close_threshold_db ?? -32}
								min={-96}
								max={0}
								step={1}
								onChange={(v) => set("noise_filter_gate_close_threshold_db", v)}
								ariaLabel={t(
									"settings.audioEnhancement.gateCloseThresholdAria",
								)}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						gateAttackLabel,
						gateAttackInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={gateAttackLabel}
							info={t("settings.audioEnhancement.gateAttackInfo")}
						>
							<RangeSlider
								value={config.noise_filter_gate_attack_ms ?? 25}
								min={0}
								max={200}
								step={1}
								onChange={(v) => set("noise_filter_gate_attack_ms", v)}
								ariaLabel={t("settings.audioEnhancement.gateAttackAria")}
								suffix="ms"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						gateHoldLabel,
						gateHoldInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={gateHoldLabel}
							info={t("settings.audioEnhancement.gateHoldInfo")}
						>
							<RangeSlider
								value={config.noise_filter_gate_hold_ms ?? 200}
								min={0}
								max={1000}
								step={10}
								onChange={(v) => set("noise_filter_gate_hold_ms", v)}
								ariaLabel={t("settings.audioEnhancement.gateHoldAria")}
								suffix="ms"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						gateReleaseLabel,
						gateReleaseInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={gateReleaseLabel}
							info={t("settings.audioEnhancement.gateReleaseInfo")}
						>
							<RangeSlider
								value={config.noise_filter_gate_release_ms ?? 150}
								min={0}
								max={1000}
								step={5}
								onChange={(v) => set("noise_filter_gate_release_ms", v)}
								ariaLabel={t("settings.audioEnhancement.gateReleaseAria")}
								suffix="ms"
								deferApply
							/>
						</SettingRow>
					)}
				</>
			)}

			{/* Equalizer */}
			{filterIsVisible(
				equalizerLabel,
				equalizerInfoSearch,
				audioSectionTitle,
			) && (
				<SettingRow
					label={equalizerLabel}
					info={t("settings.audioEnhancement.equalizerInfo")}
				>
					<Switch
						checked={config.noise_filter_eq ?? true}
						onCheckedChange={(v) => set("noise_filter_eq", v)}
						aria-label={t("settings.audioEnhancement.equalizerAria")}
					/>
				</SettingRow>
			)}
			{(config.noise_filter_eq ?? true) && (
				<>
					{filterIsVisible(eqLowLabel, eqLowInfoSearch, audioSectionTitle) && (
						<SettingRow
							label={eqLowLabel}
							info={t("settings.audioEnhancement.eqLowInfo")}
						>
							<RangeSlider
								value={config.noise_filter_eq_low_db ?? -3}
								min={-20}
								max={20}
								step={1}
								onChange={(v) => set("noise_filter_eq_low_db", v)}
								ariaLabel={t("settings.audioEnhancement.eqLowAria")}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(eqMidLabel, eqMidInfoSearch, audioSectionTitle) && (
						<SettingRow
							label={eqMidLabel}
							info={t("settings.audioEnhancement.eqMidInfo")}
						>
							<RangeSlider
								value={config.noise_filter_eq_mid_db ?? 3}
								min={-20}
								max={20}
								step={1}
								onChange={(v) => set("noise_filter_eq_mid_db", v)}
								ariaLabel={t("settings.audioEnhancement.eqMidAria")}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						eqHighLabel,
						eqHighInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={eqHighLabel}
							info={t("settings.audioEnhancement.eqHighInfo")}
						>
							<RangeSlider
								value={config.noise_filter_eq_high_db ?? 2}
								min={-20}
								max={20}
								step={1}
								onChange={(v) => set("noise_filter_eq_high_db", v)}
								ariaLabel={t("settings.audioEnhancement.eqHighAria")}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
				</>
			)}

			{/* Compressor */}
			{filterIsVisible(
				compressorLabel,
				compressorInfoSearch,
				audioSectionTitle,
			) && (
				<SettingRow
					label={compressorLabel}
					info={t("settings.audioEnhancement.compressorInfo")}
				>
					<Switch
						checked={config.noise_filter_compressor ?? true}
						onCheckedChange={(v) => set("noise_filter_compressor", v)}
						aria-label={t("settings.audioEnhancement.compressorAria")}
					/>
				</SettingRow>
			)}
			{(config.noise_filter_compressor ?? true) && (
				<>
					{filterIsVisible(
						compressorThresholdLabel,
						compressorThresholdInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={compressorThresholdLabel}
							info={t("settings.audioEnhancement.compressorThresholdInfo")}
						>
							<RangeSlider
								value={config.noise_filter_compressor_threshold_db ?? -18}
								min={-60}
								max={0}
								step={1}
								onChange={(v) => set("noise_filter_compressor_threshold_db", v)}
								ariaLabel={t(
									"settings.audioEnhancement.compressorThresholdAria",
								)}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						compressorRatioLabel,
						compressorRatioInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={compressorRatioLabel}
							info={t("settings.audioEnhancement.compressorRatioInfo")}
						>
							<RangeSlider
								value={config.noise_filter_compressor_ratio ?? 3}
								min={1}
								max={32}
								step={0.5}
								onChange={(v) => set("noise_filter_compressor_ratio", v)}
								ariaLabel={t("settings.audioEnhancement.compressorRatioAria")}
								suffix=":1"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						compressorAttackLabel,
						compressorAttackInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={compressorAttackLabel}
							info={t("settings.audioEnhancement.compressorAttackInfo")}
						>
							<RangeSlider
								value={config.noise_filter_compressor_attack_ms ?? 6}
								min={0}
								max={200}
								step={1}
								onChange={(v) => set("noise_filter_compressor_attack_ms", v)}
								ariaLabel={t("settings.audioEnhancement.compressorAttackAria")}
								suffix="ms"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						compressorReleaseLabel,
						compressorReleaseInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={compressorReleaseLabel}
							info={t("settings.audioEnhancement.compressorReleaseInfo")}
						>
							<RangeSlider
								value={config.noise_filter_compressor_release_ms ?? 60}
								min={0}
								max={1000}
								step={5}
								onChange={(v) => set("noise_filter_compressor_release_ms", v)}
								ariaLabel={t("settings.audioEnhancement.compressorReleaseAria")}
								suffix="ms"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						compressorOutputGainLabel,
						compressorOutputGainInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={compressorOutputGainLabel}
							info={t("settings.audioEnhancement.compressorOutputGainInfo")}
						>
							<RangeSlider
								value={config.noise_filter_compressor_output_gain_db ?? 0}
								min={-24}
								max={24}
								step={1}
								onChange={(v) =>
									set("noise_filter_compressor_output_gain_db", v)
								}
								ariaLabel={t(
									"settings.audioEnhancement.compressorOutputGainAria",
								)}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
				</>
			)}

			{/* Limiter */}
			{filterIsVisible(limiterLabel, limiterInfoSearch, audioSectionTitle) && (
				<SettingRow
					label={limiterLabel}
					info={t("settings.audioEnhancement.limiterInfo")}
				>
					<Switch
						checked={config.noise_filter_limiter ?? true}
						onCheckedChange={(v) => set("noise_filter_limiter", v)}
						aria-label={t("settings.audioEnhancement.limiterAria")}
					/>
				</SettingRow>
			)}
			{(config.noise_filter_limiter ?? true) && (
				<>
					{filterIsVisible(
						limiterCeilingLabel,
						limiterCeilingInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={limiterCeilingLabel}
							info={t("settings.audioEnhancement.limiterCeilingInfo")}
						>
							<RangeSlider
								value={config.noise_filter_limiter_ceiling_db ?? -6}
								min={-60}
								max={0}
								step={1}
								onChange={(v) => set("noise_filter_limiter_ceiling_db", v)}
								ariaLabel={t("settings.audioEnhancement.limiterCeilingAria")}
								suffix="dB"
								deferApply
							/>
						</SettingRow>
					)}
					{filterIsVisible(
						limiterReleaseLabel,
						limiterReleaseInfoSearch,
						audioSectionTitle,
					) && (
						<SettingRow
							label={limiterReleaseLabel}
							info={t("settings.audioEnhancement.limiterReleaseInfo")}
						>
							<RangeSlider
								value={config.noise_filter_limiter_release_ms ?? 60}
								min={0}
								max={1000}
								step={5}
								onChange={(v) => set("noise_filter_limiter_release_ms", v)}
								ariaLabel={t("settings.audioEnhancement.limiterReleaseAria")}
								suffix="ms"
								deferApply
							/>
						</SettingRow>
					)}
				</>
			)}

			{/* Notch filter */}
			{filterIsVisible(
				notchFilterLabel,
				notchFilterInfoSearch,
				audioSectionTitle,
			) && (
				<SettingRow
					label={notchFilterLabel}
					info={t("settings.audioEnhancement.notchFilterInfo")}
				>
					<Switch
						checked={config.noise_filter_notch ?? false}
						onCheckedChange={(v) => set("noise_filter_notch", v)}
						aria-label={t("settings.audioEnhancement.notchFilterAria")}
					/>
				</SettingRow>
			)}
			{(config.noise_filter_notch ?? false) &&
				filterIsVisible(
					notchFrequencyLabel,
					notchFrequencyInfoSearch,
					audioSectionTitle,
				) && (
					<SettingRow
						label={notchFrequencyLabel}
						info={t("settings.audioEnhancement.notchFrequencyInfo")}
					>
						<RangeSlider
							value={config.noise_filter_notch_frequency_hz ?? 60}
							min={50}
							max={1000}
							step={1}
							onChange={(v) => set("noise_filter_notch_frequency_hz", v)}
							ariaLabel={t("settings.audioEnhancement.notchFrequencyAria")}
							suffix="Hz"
							deferApply
						/>
					</SettingRow>
				)}
		</>
	);
}
