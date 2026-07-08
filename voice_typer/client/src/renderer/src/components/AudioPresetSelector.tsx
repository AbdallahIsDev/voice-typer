import { FilterIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { RangeSlider } from "@/components/RangeSlider";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
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

const PRESET_OPTIONS: {
	value: AudioPreset;
	label: string;
	description: string;
}[] = [
	{
		value: "auto",
		label: "Auto (recommended)",
		description: "All filters ON with RNNoise",
	},
	{
		value: "studio",
		label: "Studio",
		description: "Clean environment, minimal",
	},
	{
		value: "noisy_room",
		label: "Noisy Room",
		description: "Keyboard/fan/HVAC, DeepFilterNet",
	},
	{ value: "off", label: "Off", description: "Raw audio, no filtering" },
	{
		value: "custom",
		label: "Custom",
		description: "Advanced — configure each filter",
	},
];

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

	return (
		<div className="rounded-lg border border-border overflow-hidden">
			<button
				type="button"
				className="flex w-full items-center justify-between px-4 py-2.5 text-xs font-medium text-(--text-primary) hover:bg-(--accent)/5 transition-colors cursor-pointer"
				onClick={onToggleAdvanced}
				aria-expanded={showAdvanced}
			>
				<span className="flex items-center gap-2 font-medium tracking-wide">
					<HugeiconsIcon
						icon={FilterIcon}
						strokeWidth={2}
						className="h-4 w-4 text-(--text-muted)"
					/>
					Audio Enhancement
				</span>
				<span className="text-xs font-medium tracking-wide text-(--text-muted)">
					{PRESET_OPTIONS.find((o) => o.value === preset)?.label ??
						(preset === "custom" ? "Custom" : preset.replace("_", " "))}
				</span>
			</button>

			{showAdvanced && (
				<div className="divide-y divide-border border-t border-border">
					{/* Preset selector */}
					<div className="px-4 py-3 space-y-2">
						<p className="text-[10px] font-semibold uppercase tracking-wide text-(--text-muted)">
							Microphone Quality
						</p>
						<Select value={preset} onValueChange={handlePresetChange}>
							<SelectTrigger
								className="w-full"
								aria-label="Microphone Quality Preset"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								{PRESET_OPTIONS.map((option) => (
									<SelectItem key={option.value} value={option.value}>
										{option.label}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<p className="text-[10px] text-(--text-muted)">
							{PRESET_OPTIONS.find((o) => o.value === preset)?.description ??
								""}
						</p>
					</div>

					{/* Custom mode: individual filter controls (same as Settings.tsx Custom panel) */}
					{isCustom && (
						<div className="divide-y divide-border">
							<ToggleRow
								label="High-Pass Filter"
								description="Remove low-frequency rumble (HVAC, traffic)."
								checked={config.noise_filter_highpass ?? true}
								onChange={handleHighPassToggle}
								ariaLabel="High-Pass Filter"
							/>
							{(config.noise_filter_highpass ?? true) && (
								<SliderRow
									label="High-Pass Cutoff"
									description="Frequencies below this are attenuated."
									value={config.noise_filter_highpass_cutoff_hz ?? 80}
									min={20}
									max={500}
									step={10}
									suffix="Hz"
									onChange={handleCutoffChange}
									ariaLabel="High-Pass Cutoff"
								/>
							)}

							{/* Noise suppression method */}
							<div className="flex items-center justify-between px-4 py-2 pl-10">
								<div className="flex flex-col gap-1">
									<p className="text-xs font-medium text-(--text-primary)">
										Noise Suppression
									</p>
									<p className="text-xs text-(--text-muted)">
										Neural network denoiser backend.
									</p>
								</div>
								<Select
									value={config.noise_suppression_method ?? "rnnoise"}
									onValueChange={handleNoiseMethodChange}
								>
									<SelectTrigger
										className="w-32"
										aria-label="Noise Suppression Method"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value="rnnoise">RNNoise</SelectItem>
										<SelectItem value="deepfilternet">DeepFilterNet</SelectItem>
										<SelectItem value="speex">Speex</SelectItem>
										<SelectItem value="none">None</SelectItem>
									</SelectContent>
								</Select>
							</div>

							<ToggleRow
								label="Noise Gate"
								description="Silence audio below a threshold."
								checked={config.noise_filter_gate ?? true}
								onChange={handleGateToggle}
								ariaLabel="Noise Gate"
							/>
							{(config.noise_filter_gate ?? true) && (
								<>
									<SliderRow
										label="Gate Open Threshold"
										description="Level above which the gate opens."
										value={config.noise_filter_gate_open_threshold_db ?? -26}
										min={-96}
										max={0}
										step={1}
										suffix="dB"
										onChange={handleGateOpenChange}
										ariaLabel="Gate Open Threshold"
									/>
									<SliderRow
										label="Gate Close Threshold"
										description="Level below which the gate closes."
										value={config.noise_filter_gate_close_threshold_db ?? -32}
										min={-96}
										max={0}
										step={1}
										suffix="dB"
										onChange={handleGateCloseChange}
										ariaLabel="Gate Close Threshold"
									/>
								</>
							)}

							<ToggleRow
								label="Equalizer"
								description="3-band EQ for tone shaping."
								checked={config.noise_filter_eq ?? true}
								onChange={handleEqToggle}
								ariaLabel="Equalizer"
							/>
							{(config.noise_filter_eq ?? true) && (
								<>
									<SliderRow
										label="EQ — Low (bass)"
										description="Boost/cut below 800Hz."
										value={config.noise_filter_eq_low_db ?? -3}
										min={-20}
										max={20}
										step={1}
										suffix="dB"
										onChange={handleEqLowChange}
										ariaLabel="EQ Low"
									/>
									<SliderRow
										label="EQ — Mid (speech)"
										description="Boost/cut 800Hz–5kHz."
										value={config.noise_filter_eq_mid_db ?? 3}
										min={-20}
										max={20}
										step={1}
										suffix="dB"
										onChange={handleEqMidChange}
										ariaLabel="EQ Mid"
									/>
									<SliderRow
										label="EQ — High (treble)"
										description="Boost/cut above 5kHz."
										value={config.noise_filter_eq_high_db ?? 2}
										min={-20}
										max={20}
										step={1}
										suffix="dB"
										onChange={handleEqHighChange}
										ariaLabel="EQ High"
									/>
								</>
							)}

							<ToggleRow
								label="Compressor"
								description="Even out loud/quiet speech."
								checked={config.noise_filter_compressor ?? true}
								onChange={handleCompressorToggle}
								ariaLabel="Compressor"
							/>
							{(config.noise_filter_compressor ?? true) && (
								<>
									<SliderRow
										label="Compressor Threshold"
										description="Level above which compression starts."
										value={config.noise_filter_compressor_threshold_db ?? -18}
										min={-60}
										max={0}
										step={1}
										suffix="dB"
										onChange={handleCompressorThresholdChange}
										ariaLabel="Compressor Threshold"
									/>
									<SliderRow
										label="Compressor Ratio"
										description="How hard to compress. 3:1 gentle, 10:1 aggressive."
										value={config.noise_filter_compressor_ratio ?? 3}
										min={1}
										max={32}
										step={0.5}
										suffix=":1"
										onChange={handleCompressorRatioChange}
										ariaLabel="Compressor Ratio"
									/>
								</>
							)}

							<ToggleRow
								label="Limiter"
								description="Brick-wall ceiling to prevent clipping."
								checked={config.noise_filter_limiter ?? true}
								onChange={handleLimiterToggle}
								ariaLabel="Limiter"
							/>
							{(config.noise_filter_limiter ?? true) && (
								<SliderRow
									label="Limiter Ceiling"
									description="Absolute maximum output level."
									value={config.noise_filter_limiter_ceiling_db ?? -6}
									min={-60}
									max={0}
									step={1}
									suffix="dB"
									onChange={handleLimiterCeilingChange}
									ariaLabel="Limiter Ceiling"
								/>
							)}

							<ToggleRow
								label="Notch Filter (hum)"
								description="Remove 50/60Hz electrical mains hum."
								checked={config.noise_filter_notch ?? false}
								onChange={handleNotchToggle}
								ariaLabel="Notch Filter"
							/>
						</div>
					)}
				</div>
			)}
		</div>
	);
}
