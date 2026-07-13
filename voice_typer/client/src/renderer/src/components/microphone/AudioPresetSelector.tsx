import { FilterIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { RangeSlider } from "@/components/common/RangeSlider";
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

/**
 * ADR 0007: Audio preset dropdown + progressive-disclosure Custom panel.
 *
 * Presets are defined in ``voice_typer/server/audio_presets.py`` (single
 * source of truth). The frontend only knows the preset names + display
 * labels; the actual filter → setting mapping lives in the backend.
 *
 * When the user selects "Custom", the individual filter rows are
 * revealed (same controls as Settings.tsx → Audio → Custom panel).
 */
export type AudioPreset = "auto" | "studio" | "noisy_room" | "off" | "custom";

interface AudioPresetSelectorProps {
	preset: AudioPreset;
	/** Full config — used to read/write the individual noise_filter_* fields. */
	config: VoiceTyperConfig;
	/** Whether the collapsible panel is expanded. */
	showAdvanced: boolean;
	/** Called when the user picks a new preset. */
	onPresetChange: (preset: AudioPreset) => void;
	/** Called when the user clicks the expand/collapse header. */
	onToggleAdvanced: () => void;
	/** Called when any individual filter field changes. */
	onConfigChange: (updates: Partial<VoiceTyperConfig>) => void;
}

function getPresetOptions(): {
	value: AudioPreset;
	label: string;
	description: string;
}[] {
	return [
		{
			value: "auto",
			label: t("settings.audioEnhancement.presetAuto"),
			description: t("settings.audioEnhancement.microphoneQualityInfo"),
		},
		{
			value: "studio",
			label: t("settings.audioEnhancement.presetStudio"),
			description: t("settings.audioEnhancement.presetStudio"),
		},
		{
			value: "noisy_room",
			label: t("settings.audioEnhancement.presetNoisyRoom"),
			description: t("settings.audioEnhancement.presetNoisyRoom"),
		},
		{
			value: "off",
			label: t("settings.audioEnhancement.presetOff"),
			description: t("settings.audioEnhancement.presetOff"),
		},
		{
			value: "custom",
			label: t("settings.audioEnhancement.presetCustom"),
			description: t("settings.audioEnhancement.presetCustom"),
		},
	];
}

function ToggleRow({
	label,
	description,
	checked,
	onChange,
	ariaLabel,
}: {
	label: string;
	description: string;
	checked: boolean;
	onChange: (checked: boolean) => void;
	ariaLabel: string;
}) {
	return (
		<div className="flex items-center justify-between px-4 py-2 pl-10">
			<div className="flex flex-col gap-1">
				<p className="text-xs font-medium text-(--text-primary)">{label}</p>
				<p className="text-xs text-(--text-muted)">{description}</p>
			</div>
			<Switch
				checked={checked}
				onCheckedChange={onChange}
				aria-label={ariaLabel}
			/>
		</div>
	);
}

function SliderRow({
	label,
	description,
	value,
	min,
	max,
	step,
	suffix,
	onChange,
	ariaLabel,
}: {
	label: string;
	description: string;
	value: number;
	min: number;
	max: number;
	step: number;
	suffix: string;
	onChange: (value: number) => void;
	ariaLabel: string;
}) {
	return (
		<div className="flex items-center justify-between px-4 py-2 pl-10">
			<div className="flex flex-col gap-1">
				<p className="text-xs font-medium text-(--text-primary)">{label}</p>
				<p className="text-xs text-(--text-muted)">{description}</p>
			</div>
			<RangeSlider
				value={value}
				min={min}
				max={max}
				step={step}
				onChange={onChange}
				ariaLabel={ariaLabel}
				suffix={suffix}
			/>
		</div>
	);
}
export function AudioPresetSelector({
	preset,
	config,
	showAdvanced,
	onPresetChange,
	onToggleAdvanced,
	onConfigChange,
}: AudioPresetSelectorProps) {
	const isCustom = preset === "custom";

	const handlePresetChange = (v: string) => onPresetChange(v as AudioPreset);
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
	const handleLimiterToggle = (v: boolean) =>
		onConfigChange({ noise_filter_limiter: v });
	const handleLimiterCeilingChange = (v: number) =>
		onConfigChange({ noise_filter_limiter_ceiling_db: v });
	const handleNotchToggle = (v: boolean) =>
		onConfigChange({ noise_filter_notch: v });

	const panelId = "audio-preset-panel";

	return (
		<div className="rounded-lg border border-border overflow-hidden">
			<button
				type="button"
				className="flex w-full items-center justify-between px-4 py-2.5 text-xs font-medium text-(--text-primary) hover:bg-(--accent)/5 transition-colors cursor-pointer"
				onClick={onToggleAdvanced}
				aria-expanded={showAdvanced}
				aria-controls={panelId}
			>
				<span className="flex items-center gap-2 font-medium tracking-wide">
					<HugeiconsIcon
						icon={FilterIcon}
						strokeWidth={2}
						className="h-4 w-4 text-(--text-muted)"
					/>
					{t("settings.audioEnhancement.title")}
				</span>
				<span className="text-xs font-medium tracking-wide text-(--text-muted)">
					{getPresetOptions().find((o) => o.value === preset)?.label ??
						(preset === "custom"
							? t("settings.audioEnhancement.presetCustom")
							: preset.replace("_", " "))}
				</span>
			</button>

			{showAdvanced && (
				<div
					id={panelId}
					className="divide-y divide-border border-t border-border"
				>
					{/* Preset selector */}
					<div className="px-4 py-3 space-y-2">
						<p className="text-[10px] font-semibold uppercase tracking-wide text-(--text-muted)">
							{t("settings.audioEnhancement.microphoneQuality")}
						</p>
						<Select value={preset} onValueChange={handlePresetChange}>
							<SelectTrigger
								className="w-full"
								aria-label={t("a11y.microphoneQualityPreset")}
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{getPresetOptions().map((option) => (
									<SelectItem key={option.value} value={option.value}>
										{option.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<p className="text-[10px] text-(--text-muted)">
							{getPresetOptions().find((o) => o.value === preset)
								?.description ?? ""}
						</p>
					</div>

					{/* Custom mode: individual filter controls (same as Settings.tsx Custom panel) */}
					{isCustom && (
						<div className="divide-y divide-border">
							<ToggleRow
								label={t("settings.audioEnhancement.highPassFilter")}
								description={t("settings.audioEnhancement.highPassFilterInfo")}
								checked={config.noise_filter_highpass ?? true}
								onChange={handleHighPassToggle}
								ariaLabel={t("audioPreset.highPassFilter")}
							/>
							{(config.noise_filter_highpass ?? true) && (
								<SliderRow
									label={t("settings.audioEnhancement.highPassCutoff")}
									description={t(
										"settings.audioEnhancement.highPassCutoffInfo",
									)}
									value={config.noise_filter_highpass_cutoff_hz ?? 80}
									min={20}
									max={500}
									step={10}
									suffix="Hz"
									onChange={handleCutoffChange}
									ariaLabel={t("audioPreset.highPassCutoff")}
								/>
							)}

							{/* Noise suppression method */}
							<div className="flex items-center justify-between px-4 py-2 pl-10">
								<div className="flex flex-col gap-1">
									<p className="text-xs font-medium text-(--text-primary)">
										{t("settings.audioEnhancement.noiseSuppression")}
									</p>
									<p className="text-xs text-(--text-muted)">
										{t("settings.audioEnhancement.noiseSuppressionInfo")}
									</p>
								</div>
								<Select
									value={config.noise_suppression_method ?? "rnnoise"}
									onValueChange={handleNoiseMethodChange}
								>
									<SelectTrigger
										className="w-32"
										aria-label={t("a11y.noiseSuppressionMethod")}
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value="rnnoise">RNNoise</SelectItem>
										<SelectItem value="deepfilternet">DeepFilterNet</SelectItem>
										<SelectItem value="speex">Speex</SelectItem>
										<SelectItem value="none">
											{t("settings.audioEnhancement.noneOption")}
										</SelectItem>
									</SelectContent>
								</Select>
							</div>

							<ToggleRow
								label={t("settings.audioEnhancement.noiseGate")}
								description={t("settings.audioEnhancement.noiseGateInfo")}
								checked={config.noise_filter_gate ?? true}
								onChange={handleGateToggle}
								ariaLabel={t("audioPreset.noiseGate")}
							/>
							{(config.noise_filter_gate ?? true) && (
								<>
									<SliderRow
										label={t("settings.audioEnhancement.gateOpenThreshold")}
										description={t(
											"settings.audioEnhancement.gateOpenThresholdInfo",
										)}
										value={config.noise_filter_gate_open_threshold_db ?? -26}
										min={-96}
										max={0}
										step={1}
										suffix="dB"
										onChange={handleGateOpenChange}
										ariaLabel={t("audioPreset.gateOpenThreshold")}
									/>
									<SliderRow
										label={t("settings.audioEnhancement.gateCloseThreshold")}
										description={t(
											"settings.audioEnhancement.gateCloseThresholdInfo",
										)}
										value={config.noise_filter_gate_close_threshold_db ?? -32}
										min={-96}
										max={0}
										step={1}
										suffix="dB"
										onChange={handleGateCloseChange}
										ariaLabel={t("audioPreset.gateCloseThreshold")}
									/>
								</>
							)}

							<ToggleRow
								label={t("settings.audioEnhancement.equalizer")}
								description={t("settings.audioEnhancement.equalizerInfo")}
								checked={config.noise_filter_eq ?? true}
								onChange={handleEqToggle}
								ariaLabel={t("audioPreset.equalizer")}
							/>
							{(config.noise_filter_eq ?? true) && (
								<>
									<SliderRow
										label={t("settings.audioEnhancement.eqLow")}
										description={t("settings.audioEnhancement.eqLowInfo")}
										value={config.noise_filter_eq_low_db ?? -3}
										min={-20}
										max={20}
										step={1}
										suffix="dB"
										onChange={handleEqLowChange}
										ariaLabel={t("audioPreset.eqLow")}
									/>
									<SliderRow
										label={t("settings.audioEnhancement.eqMid")}
										description={t("settings.audioEnhancement.eqMidInfo")}
										value={config.noise_filter_eq_mid_db ?? 3}
										min={-20}
										max={20}
										step={1}
										suffix="dB"
										onChange={handleEqMidChange}
										ariaLabel={t("audioPreset.eqMid")}
									/>
									<SliderRow
										label={t("settings.audioEnhancement.eqHigh")}
										description={t("settings.audioEnhancement.eqHighInfo")}
										value={config.noise_filter_eq_high_db ?? 2}
										min={-20}
										max={20}
										step={1}
										suffix="dB"
										onChange={handleEqHighChange}
										ariaLabel={t("audioPreset.eqHigh")}
									/>
								</>
							)}

							<ToggleRow
								label={t("settings.audioEnhancement.compressor")}
								description={t("settings.audioEnhancement.compressorInfo")}
								checked={config.noise_filter_compressor ?? true}
								onChange={handleCompressorToggle}
								ariaLabel={t("audioPreset.compressor")}
							/>
							{(config.noise_filter_compressor ?? true) && (
								<>
									<SliderRow
										label={t("settings.audioEnhancement.compressorThreshold")}
										description={t(
											"settings.audioEnhancement.compressorThresholdInfo",
										)}
										value={config.noise_filter_compressor_threshold_db ?? -18}
										min={-60}
										max={0}
										step={1}
										suffix="dB"
										onChange={handleCompressorThresholdChange}
										ariaLabel={t("audioPreset.compressorThreshold")}
									/>
									<SliderRow
										label={t("settings.audioEnhancement.compressorRatio")}
										description={t(
											"settings.audioEnhancement.compressorRatioInfo",
										)}
										value={config.noise_filter_compressor_ratio ?? 3}
										min={1}
										max={32}
										step={0.5}
										suffix=":1"
										onChange={handleCompressorRatioChange}
										ariaLabel={t("audioPreset.compressorRatio")}
									/>
								</>
							)}

							<ToggleRow
								label={t("settings.audioEnhancement.limiter")}
								description={t("settings.audioEnhancement.limiterInfo")}
								checked={config.noise_filter_limiter ?? true}
								onChange={handleLimiterToggle}
								ariaLabel={t("audioPreset.limiter")}
							/>
							{(config.noise_filter_limiter ?? true) && (
								<SliderRow
									label={t("settings.audioEnhancement.limiterCeiling")}
									description={t(
										"settings.audioEnhancement.limiterCeilingInfo",
									)}
									value={config.noise_filter_limiter_ceiling_db ?? -6}
									min={-60}
									max={0}
									step={1}
									suffix="dB"
									onChange={handleLimiterCeilingChange}
									ariaLabel={t("audioPreset.limiterCeiling")}
								/>
							)}

							<ToggleRow
								label={t("settings.audioEnhancement.notchFilter")}
								description={t("settings.audioEnhancement.notchFilterInfo")}
								checked={config.noise_filter_notch ?? false}
								onChange={handleNotchToggle}
								ariaLabel={t("audioPreset.notchFilter")}
							/>
						</div>
					)}
				</div>
			)}
		</div>
	);
}
