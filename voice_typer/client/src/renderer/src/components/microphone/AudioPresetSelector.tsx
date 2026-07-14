import { FilterIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { AudioFilterChain } from "@/components/audio/AudioFilterChain";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
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

					{/* F-1: filter chain extracted to shared <AudioFilterChain />. */}
					{isCustom && (
						<AudioFilterChain config={config} onConfigChange={onConfigChange} />
					)}
				</div>
			)}
		</div>
	);
}
