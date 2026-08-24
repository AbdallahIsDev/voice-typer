// Accordion-style "Microphone Quality" preset selector — Microphone
// page only.
//
// Collapsed, the header shows the section label + the CURRENT selection;
// expanding reveals a RadioGroup of the five presets (single source of
// truth for the preset set: `voice_typer/server/audio_presets.py` — the
// labels/descriptions here mirror AudioPresetSelector's data but the
// component is intentionally NOT imported so the Settings surface and
// this one can evolve independently).
//
// Selecting a radio applies the preset immediately (ADR 0007: backend
// maps preset → filter chain). When the Custom preset is active, the
// progressive-disclosure Custom-filters toggle + AudioFilterChain render
// inside the expanded region (same behaviour as AudioPresetSelector's
// custom panel).
//
// The expand/collapse affordance is the shared ui/accordion trigger —
// its persistent PlusSignIcon stays "+" in both states (app-wide
// accordion convention).

import { ArrowDown01Icon, FilterIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { MouseEvent } from "react";
import { useMemo } from "react";
import { AudioFilterChain } from "@/components/audio/AudioFilterChain";
import type { AudioPreset } from "@/components/microphone/AudioPresetSelector";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";

interface PresetAccordionSelectorProps {
	preset: AudioPreset;
	/** Full config — used to read/write the individual noise_filter_* fields. */
	config: VoiceTyperConfig;
	/** Whether the Custom-filters panel is expanded. */
	showAdvanced: boolean;
	/** Called when the user picks a new preset. */
	onPresetChange: (preset: AudioPreset) => void;
	/** Called when the user toggles the Custom-filters disclosure. */
	onToggleAdvanced: () => void;
	/** Called when any individual filter field changes. */
	onConfigChange: (updates: Partial<VoiceTyperConfig>) => void;
}

interface PresetOption {
	value: AudioPreset;
	label: string;
	description: string;
}

function getPresetOptions(): PresetOption[] {
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

const ACCORDION_ITEM_VALUE = "microphone-quality";

/**
 * Row-click handler for a preset option. Clicks originating on the
 * radio control itself are left to Radix (onValueChange); handling them
 * here too would apply the preset twice for one click.
 */
function makeOptionClickHandler(
	preset: AudioPreset,
	onPresetChange: (preset: AudioPreset) => void,
	value: AudioPreset,
): (event: MouseEvent<HTMLDivElement>) => void {
	return (event) => {
		if (
			(event.target as HTMLElement).closest('[data-slot="radio-group-item"]')
		) {
			return;
		}
		if (preset === value) return;
		onPresetChange(value);
	};
}

export function PresetAccordionSelector({
	preset,
	config,
	showAdvanced,
	onPresetChange,
	onToggleAdvanced,
	onConfigChange,
}: PresetAccordionSelectorProps) {
	const presetOptions = useMemo(() => getPresetOptions(), []);
	const current = presetOptions.find((o) => o.value === preset);
	const isCustom = preset === "custom";
	const panelId = "mic-preset-custom-panel";

	return (
		<Accordion
			type="single"
			collapsible
			className="rounded-lg border border-border/10 bg-(--bg-subtle) overflow-hidden"
		>
			<AccordionItem
				value={ACCORDION_ITEM_VALUE}
				className="border-border/10 data-open:bg-transparent"
			>
				<AccordionTrigger
					// No aria-label override: the visible trigger content (section
					// label + CURRENT selection) IS the accessible name, so screen
					// readers announce the selected preset in the collapsed state.
					className="items-center gap-3 px-4 py-3 hover:bg-foreground/5 hover:no-underline focus-visible:ring-ring/30 **:data-[slot=accordion-trigger-icon]:text-(--text-muted)"
				>
					<span className="flex flex-col items-start gap-1 min-w-0 text-start">
						<span className="text-xs font-semibold uppercase tracking-wide text-(--text-muted)">
							{t("settings.audioEnhancement.microphoneQuality")}
						</span>
						<span
							className="text-sm font-medium text-(--text-primary) truncate"
							data-testid="mic-preset-current"
						>
							{current?.label ?? preset}
						</span>
					</span>
				</AccordionTrigger>
				<AccordionContent className="px-4 pb-4 pt-0">
					<div className="space-y-2">
						<p className="text-xs text-(--text-muted)">
							{current?.description ?? ""}
						</p>
						<RadioGroup
							value={preset}
							onValueChange={(v) => onPresetChange(v as AudioPreset)}
							className="gap-0.5"
							data-testid="mic-preset-radio-group"
						>
							{presetOptions.map((option) => (
								// biome-ignore lint/a11y/noStaticElementInteractions: the nested RadioGroupItem is the accessible control (role=radio); the row click is pointer convenience.
								// biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation goes through the focused radio itself (Space/arrows via Radix); a keydown mirror here would double-fire the selection.
								<div
									key={option.value}
									className="flex items-start gap-3 rounded-lg px-2 py-2 cursor-pointer transition-colors hover:bg-foreground/5"
									data-testid={`mic-preset-option-${option.value}`}
									onClick={makeOptionClickHandler(
										preset,
										onPresetChange,
										option.value,
									)}
								>
									{/* Explicit aria-label keeps the radio's accessible
								    name to the preset LABEL — an implicit-label
								    fallback would concatenate the whole row text. */}
									<RadioGroupItem
										value={option.value}
										className="mt-0.5"
										aria-label={option.label}
									/>
									<span className="flex flex-col gap-0.5 min-w-0">
										<span className="text-sm font-medium text-(--text-primary)">
											{option.label}
										</span>
										<span className="text-xs text-(--text-muted)">
											{option.description}
										</span>
									</span>
								</div>
							))}
						</RadioGroup>
					</div>

					{isCustom && (
						<>
							<button
								type="button"
								className="mt-2 flex w-full items-center justify-between rounded-lg border border-border/10 px-3 py-2.5 text-xs font-medium text-(--text-primary) hover:bg-foreground/5 transition-colors cursor-pointer"
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
									{t("settings.audioEnhancement.customFiltersTitle")}
								</span>
								<HugeiconsIcon
									icon={ArrowDown01Icon}
									strokeWidth={1.625}
									className={cn(
										"h-3.5 w-3.5 transition-transform duration-200",
										showAdvanced && "rotate-180",
									)}
								/>
							</button>

							{showAdvanced && (
								<div id={panelId} className="mt-2">
									<AudioFilterChain
										config={config}
										onConfigChange={onConfigChange}
									/>
								</div>
							)}
						</>
					)}
				</AccordionContent>
			</AccordionItem>
		</Accordion>
	);
}
