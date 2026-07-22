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
}

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
 */
export function AudioFilterChain({
	config,
	onConfigChange,
}: AudioFilterChainProps) {
	const handleHighPassToggle = (v: boolean) =>
		onConfigChange({ noise_filter_highpass: v });
	const handleCutoffChange = (v: number) =>
		onConfigChange({ noise_filter_highpass_cutoff_hz: v });
	const handleNoiseMethodChange = (v: string) =>
		onConfigChange({
			noise_suppression_method:
				v as VoiceTyperConfig["noise_suppression_method"],
		});
	const handleGateToggle = (v: boolean) =>
		onConfigChange({ noise_filter_gate: v });
	const handleGateOpenChange = (v: number) =>
		onConfigChange({ noise_filter_gate_open_threshold_db: v });
	const handleGateCloseChange = (v: number) =>
		onConfigChange({ noise_filter_gate_close_threshold_db: v });
	const handleGateAttackChange = (v: number) =>
		onConfigChange({ noise_filter_gate_attack_ms: v });
	const handleGateHoldChange = (v: number) =>
		onConfigChange({ noise_filter_gate_hold_ms: v });
	const handleGateReleaseChange = (v: number) =>
		onConfigChange({ noise_filter_gate_release_ms: v });
	const handleEqToggle = (v: boolean) => onConfigChange({ noise_filter_eq: v });
	const handleEqLowChange = (v: number) =>
		onConfigChange({ noise_filter_eq_low_db: v });
	const handleEqMidChange = (v: number) =>
		onConfigChange({ noise_filter_eq_mid_db: v });
	const handleEqHighChange = (v: number) =>
		onConfigChange({ noise_filter_eq_high_db: v });
	const handleCompressorToggle = (v: boolean) =>
		onConfigChange({ noise_filter_compressor: v });
	const handleCompressorThresholdChange = (v: number) =>
		onConfigChange({ noise_filter_compressor_threshold_db: v });
	const handleCompressorRatioChange = (v: number) =>
		onConfigChange({ noise_filter_compressor_ratio: v });
	const handleCompressorAttackChange = (v: number) =>
		onConfigChange({ noise_filter_compressor_attack_ms: v });
	const handleCompressorReleaseChange = (v: number) =>
		onConfigChange({ noise_filter_compressor_release_ms: v });
	const handleCompressorOutputGainChange = (v: number) =>
		onConfigChange({ noise_filter_compressor_output_gain_db: v });
	const handleLimiterToggle = (v: boolean) =>
		onConfigChange({ noise_filter_limiter: v });
	const handleLimiterCeilingChange = (v: number) =>
		onConfigChange({ noise_filter_limiter_ceiling_db: v });
	const handleLimiterReleaseChange = (v: number) =>
		onConfigChange({ noise_filter_limiter_release_ms: v });
	const handleNotchToggle = (v: boolean) =>
		onConfigChange({ noise_filter_notch: v });
	const handleNotchFrequencyChange = (v: number) =>
		onConfigChange({ noise_filter_notch_frequency_hz: v });

	return (
		<>
			{/* High-pass filter */}
			<SettingRow
				label={t("settings.audioEnhancement.highPassFilter")}
				info={t("settings.audioEnhancement.highPassFilterInfo")}
			>
				<Switch
					checked={config.noise_filter_highpass ?? true}
					onCheckedChange={handleHighPassToggle}
					aria-label={t("settings.audioEnhancement.highPassFilterAria")}
				/>
			</SettingRow>
			{(config.noise_filter_highpass ?? true) && (
				<SettingRow
					label={t("settings.audioEnhancement.highPassCutoff")}
					info={t("settings.audioEnhancement.highPassCutoffInfo")}
				>
					<RangeSlider
						value={config.noise_filter_highpass_cutoff_hz ?? 80}
						min={20}
						max={500}
						step={10}
						onChange={handleCutoffChange}
						ariaLabel={t("settings.audioEnhancement.highPassCutoffAria")}
						suffix="Hz"
						deferApply
					/>
				</SettingRow>
			)}

			{/* Noise suppression method */}
			<SettingRow
				label={t("settings.audioEnhancement.noiseSuppression")}
				info={t("settings.audioEnhancement.noiseSuppressionInfo")}
			>
				<Select
					value={config.noise_suppression_method ?? "rnnoise"}
					onValueChange={handleNoiseMethodChange}
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

			{/* Noise gate */}
			<SettingRow
				label={t("settings.audioEnhancement.noiseGate")}
				info={t("settings.audioEnhancement.noiseGateInfo")}
			>
				<Switch
					checked={config.noise_filter_gate ?? true}
					onCheckedChange={handleGateToggle}
					aria-label={t("settings.audioEnhancement.noiseGateAria")}
				/>
			</SettingRow>
			{(config.noise_filter_gate ?? true) && (
				<>
					<SettingRow
						label={t("settings.audioEnhancement.gateOpenThreshold")}
						info={t("settings.audioEnhancement.gateOpenThresholdInfo")}
					>
						<RangeSlider
							value={config.noise_filter_gate_open_threshold_db ?? -26}
							min={-96}
							max={0}
							step={1}
							onChange={handleGateOpenChange}
							ariaLabel={t("settings.audioEnhancement.gateOpenThresholdAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.gateCloseThreshold")}
						info={t("settings.audioEnhancement.gateCloseThresholdInfo")}
					>
						<RangeSlider
							value={config.noise_filter_gate_close_threshold_db ?? -32}
							min={-96}
							max={0}
							step={1}
							onChange={handleGateCloseChange}
							ariaLabel={t("settings.audioEnhancement.gateCloseThresholdAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.gateAttack")}
						info={t("settings.audioEnhancement.gateAttackInfo")}
					>
						<RangeSlider
							value={config.noise_filter_gate_attack_ms ?? 25}
							min={0}
							max={200}
							step={1}
							onChange={handleGateAttackChange}
							ariaLabel={t("settings.audioEnhancement.gateAttackAria")}
							suffix="ms"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.gateHold")}
						info={t("settings.audioEnhancement.gateHoldInfo")}
					>
						<RangeSlider
							value={config.noise_filter_gate_hold_ms ?? 200}
							min={0}
							max={1000}
							step={10}
							onChange={handleGateHoldChange}
							ariaLabel={t("settings.audioEnhancement.gateHoldAria")}
							suffix="ms"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.gateRelease")}
						info={t("settings.audioEnhancement.gateReleaseInfo")}
					>
						<RangeSlider
							value={config.noise_filter_gate_release_ms ?? 150}
							min={0}
							max={1000}
							step={5}
							onChange={handleGateReleaseChange}
							ariaLabel={t("settings.audioEnhancement.gateReleaseAria")}
							suffix="ms"
							deferApply
						/>
					</SettingRow>
				</>
			)}

			{/* Equalizer */}
			<SettingRow
				label={t("settings.audioEnhancement.equalizer")}
				info={t("settings.audioEnhancement.equalizerInfo")}
			>
				<Switch
					checked={config.noise_filter_eq ?? true}
					onCheckedChange={handleEqToggle}
					aria-label={t("settings.audioEnhancement.equalizerAria")}
				/>
			</SettingRow>
			{(config.noise_filter_eq ?? true) && (
				<>
					<SettingRow
						label={t("settings.audioEnhancement.eqLow")}
						info={t("settings.audioEnhancement.eqLowInfo")}
					>
						<RangeSlider
							value={config.noise_filter_eq_low_db ?? -3}
							min={-20}
							max={20}
							step={1}
							onChange={handleEqLowChange}
							ariaLabel={t("settings.audioEnhancement.eqLowAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.eqMid")}
						info={t("settings.audioEnhancement.eqMidInfo")}
					>
						<RangeSlider
							value={config.noise_filter_eq_mid_db ?? 3}
							min={-20}
							max={20}
							step={1}
							onChange={handleEqMidChange}
							ariaLabel={t("settings.audioEnhancement.eqMidAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.eqHigh")}
						info={t("settings.audioEnhancement.eqHighInfo")}
					>
						<RangeSlider
							value={config.noise_filter_eq_high_db ?? 2}
							min={-20}
							max={20}
							step={1}
							onChange={handleEqHighChange}
							ariaLabel={t("settings.audioEnhancement.eqHighAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
				</>
			)}

			{/* Compressor */}
			<SettingRow
				label={t("settings.audioEnhancement.compressor")}
				info={t("settings.audioEnhancement.compressorInfo")}
			>
				<Switch
					checked={config.noise_filter_compressor ?? true}
					onCheckedChange={handleCompressorToggle}
					aria-label={t("settings.audioEnhancement.compressorAria")}
				/>
			</SettingRow>
			{(config.noise_filter_compressor ?? true) && (
				<>
					<SettingRow
						label={t("settings.audioEnhancement.compressorThreshold")}
						info={t("settings.audioEnhancement.compressorThresholdInfo")}
					>
						<RangeSlider
							value={config.noise_filter_compressor_threshold_db ?? -18}
							min={-60}
							max={0}
							step={1}
							onChange={handleCompressorThresholdChange}
							ariaLabel={t("settings.audioEnhancement.compressorThresholdAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.compressorRatio")}
						info={t("settings.audioEnhancement.compressorRatioInfo")}
					>
						<RangeSlider
							value={config.noise_filter_compressor_ratio ?? 3}
							min={1}
							max={32}
							step={0.5}
							onChange={handleCompressorRatioChange}
							ariaLabel={t("settings.audioEnhancement.compressorRatioAria")}
							suffix=":1"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.compressorAttack")}
						info={t("settings.audioEnhancement.compressorAttackInfo")}
					>
						<RangeSlider
							value={config.noise_filter_compressor_attack_ms ?? 6}
							min={0}
							max={200}
							step={1}
							onChange={handleCompressorAttackChange}
							ariaLabel={t("settings.audioEnhancement.compressorAttackAria")}
							suffix="ms"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.compressorRelease")}
						info={t("settings.audioEnhancement.compressorReleaseInfo")}
					>
						<RangeSlider
							value={config.noise_filter_compressor_release_ms ?? 60}
							min={0}
							max={1000}
							step={5}
							onChange={handleCompressorReleaseChange}
							ariaLabel={t("settings.audioEnhancement.compressorReleaseAria")}
							suffix="ms"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.compressorOutputGain")}
						info={t("settings.audioEnhancement.compressorOutputGainInfo")}
					>
						<RangeSlider
							value={config.noise_filter_compressor_output_gain_db ?? 0}
							min={-24}
							max={24}
							step={1}
							onChange={handleCompressorOutputGainChange}
							ariaLabel={t(
								"settings.audioEnhancement.compressorOutputGainAria",
							)}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
				</>
			)}

			{/* Limiter */}
			<SettingRow
				label={t("settings.audioEnhancement.limiter")}
				info={t("settings.audioEnhancement.limiterInfo")}
			>
				<Switch
					checked={config.noise_filter_limiter ?? true}
					onCheckedChange={handleLimiterToggle}
					aria-label={t("settings.audioEnhancement.limiterAria")}
				/>
			</SettingRow>
			{(config.noise_filter_limiter ?? true) && (
				<>
					<SettingRow
						label={t("settings.audioEnhancement.limiterCeiling")}
						info={t("settings.audioEnhancement.limiterCeilingInfo")}
					>
						<RangeSlider
							value={config.noise_filter_limiter_ceiling_db ?? -6}
							min={-60}
							max={0}
							step={1}
							onChange={handleLimiterCeilingChange}
							ariaLabel={t("settings.audioEnhancement.limiterCeilingAria")}
							suffix="dB"
							deferApply
						/>
					</SettingRow>
					<SettingRow
						label={t("settings.audioEnhancement.limiterRelease")}
						info={t("settings.audioEnhancement.limiterReleaseInfo")}
					>
						<RangeSlider
							value={config.noise_filter_limiter_release_ms ?? 60}
							min={0}
							max={1000}
							step={5}
							onChange={handleLimiterReleaseChange}
							ariaLabel={t("settings.audioEnhancement.limiterReleaseAria")}
							suffix="ms"
							deferApply
						/>
					</SettingRow>
				</>
			)}

			{/* Notch filter */}
			<SettingRow
				label={t("settings.audioEnhancement.notchFilter")}
				info={t("settings.audioEnhancement.notchFilterInfo")}
			>
				<Switch
					checked={config.noise_filter_notch ?? false}
					onCheckedChange={handleNotchToggle}
					aria-label={t("settings.audioEnhancement.notchFilterAria")}
				/>
			</SettingRow>
			{(config.noise_filter_notch ?? false) && (
				<SettingRow
					label={t("settings.audioEnhancement.notchFrequency")}
					info={t("settings.audioEnhancement.notchFrequencyInfo")}
				>
					<RangeSlider
						value={config.noise_filter_notch_frequency_hz ?? 60}
						min={50}
						max={1000}
						step={1}
						onChange={handleNotchFrequencyChange}
						ariaLabel={t("settings.audioEnhancement.notchFrequencyAria")}
						suffix="Hz"
						deferApply
					/>
				</SettingRow>
			)}
		</>
	);
}
