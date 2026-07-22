import { ArrowDown01Icon, FilterIcon } from "@hugeicons/core-free-icons";
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
import { cn } from "@/lib/utils";
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

/**
 * Fix 6: Each preset now has a *real* description (distinct from the
 * label). Previously the Studio / Noisy Room / Off / Custom presets all
 * reused their own label as the description, leaving the user with no
 * explanation of what the preset actually does. The description keys
 * live under `settings.audioEnhancement.preset*Description` in en.json.
 */
function getPresetOptions(): {
	value: AudioPreset;
	label: string;
	description: string;
}[] {
	return [
		{
			value: "auto",
			label: t("settings.audioEnhancement.presetAuto"),
			description: t("settings.audioEnhancement.presetAutoDescription"),
		},
		{
			value: "studio",
			label: t("settings.audioEnhancement.presetStudio"),
			description: t("settings.audioEnhancement.presetStudioDescription"),
		},
		{
			value: "noisy_room",
			label: t("settings.audioEnhancement.presetNoisyRoom"),
			description: t("settings.audioEnhancement.presetNoisyRoomDescription"),
		},
		{
			value: "off",
			label: t("settings.audioEnhancement.presetOff"),
			description: t("settings.audioEnhancement.presetOffDescription"),
		},
		{
			value: "custom",
			label: t("settings.audioEnhancement.presetCustom"),
			description: t("settings.audioEnhancement.presetCustomDescription"),
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
				<span className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-(--text-muted)">
					{getPresetOptions().find((o) => o.value === preset)?.label ??
						(preset === "custom"
							? t("settings.audioEnhancement.presetCustom")
							: preset.replace("_", " "))}
					{/* Fix 12: chevron with rotation animation so users can see
					    at a glance whether the panel is expanded. The rotation
					    is CSS-driven (no JS state needed beyond `showAdvanced`). */}
					<HugeiconsIcon
						icon={ArrowDown01Icon}
						strokeWidth={1.625}
						className={cn(
							"h-3.5 w-3.5 transition-transform duration-200",
							showAdvanced && "rotate-180",
						)}
					/>
				</span>
			</button>

			{showAdvanced && (
				<div
					id={panelId}
					className="divide-y divide-border border-t border-border"
				>
					{/* Preset selector */}
					<div className="px-4 py-3 space-y-2">
						<p className="text-xs font-semibold uppercase tracking-wide text-(--text-muted)">
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
						<p className="text-xs text-(--text-muted)">
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
