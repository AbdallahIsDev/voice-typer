// Accordion-style "Microphone Quality" preset selector — Microphone
// page only.
//
// Collapsed, the header shows the section label + a general help
// InfoTooltip + the CURRENT selection; expanding reveals a RadioGroup
// of the five presets (single source of truth for the preset set:
// `voice_typer/server/audio_presets.py` — the labels/descriptions here
// mirror AudioPresetSelector's data but the component is intentionally
// NOT imported so the Settings surface and this one can evolve
// independently). Each option's description lives in a per-row
// InfoTooltip instead of being permanently visible — keeps the
// accordion compact and kills the duplicated header paragraph.
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
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
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
 * Row-click handler for a preset option. Clicks originating on any
 * native <button> inside the row are left alone: the Radix radio
 * control handles selection itself via onValueChange (handling it here
 * too would apply the preset twice for one click), and the row's
 * InfoTooltip trigger opens the description tooltip — it must never
 * change the preset as a side effect.
 */
function makeOptionClickHandler(
	preset: AudioPreset,
	onPresetChange: (preset: AudioPreset) => void,
	value: AudioPreset,
): (event: MouseEvent<HTMLDivElement>) => void {
	return (event) => {
		if ((event.target as HTMLElement).closest("button")) {
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
			className="rounded-lg border border-border/5 bg-(--bg-subtle) overflow-hidden"
		>
			<AccordionItem
				value={ACCORDION_ITEM_VALUE}
				className="border-border/5 data-open:bg-transparent"
			>
				<AccordionTrigger
					// No aria-label override: the visible trigger content (section
					// label + CURRENT selection field) IS the accessible name, so
					// screen readers announce the selected preset in the collapsed
					// state. Single compact row: label + ? on the left, the
					// active-filter field + rotating chevron grouped on the right
					// (base justify-between spreads them). px-4 keeps both insets
					// equal and matches the option rows' content boundary.
					// The primitive's persistent PlusSignIcon is hidden on THIS
					// instance (user decision for this selector) and replaced by
					// the dedicated rotating chevron below — the primitive itself
					// is untouched, so every other accordion keeps its "+".
					className="items-center gap-3 px-4 py-2.5 hover:bg-foreground/5 hover:no-underline focus-visible:ring-ring/30 **:data-[slot=accordion-trigger-icon]:hidden **:data-[slot=accordion-trigger-icon]:text-(--text-muted)"
				>
					<span className="flex items-center gap-1.5 min-w-0">
						<span className="text-xs font-semibold uppercase tracking-wide text-(--text-muted)">
							{t("settings.audioEnhancement.microphoneQuality")}
						</span>
						{/* Inline (span) trigger: this tooltip lives INSIDE the
						AccordionTrigger <button>, where a nested real <button>
						would be invalid DOM and would toggle the accordion.
						Anchored in normal flow beside the label — it can never
						slide into the option list during the expand animation. */}
						<InfoTooltip
							triggerAs="inline"
							text={t("settings.audioEnhancement.microphoneQualityInfo")}
							contextLabel={t("settings.audioEnhancement.microphoneQuality")}
						/>
					</span>
					{/* Active-filter field + chevron: a NON-interactive chip
					(plain span — the whole row is the one control) that visually
					communicates the current selection, grouped with the expand
					control it belongs to. Mirrors the SelectTrigger shell
					(bg-background lifts it off the bg-(--bg-subtle) card). The
					chevron is decorative (the trigger owns aria-expanded) and
					rotates via the primitive's data-state — collapsed points
					down (can expand), expanded points up (can collapse). */}
					<span className="flex items-center gap-2 shrink-0">
						<span
							className="inline-flex max-w-40 items-center rounded-md border border-border/5 bg-background px-2.5 py-1 text-xs font-medium text-(--text-primary)"
							data-testid="mic-preset-current"
						>
							<span className="truncate">{current?.label ?? preset}</span>
						</span>
						<HugeiconsIcon
							icon={ArrowDown01Icon}
							strokeWidth={1.625}
							aria-hidden="true"
							className="size-4 shrink-0 text-(--text-muted) transition-transform duration-200 group-data-[state=open]/accordion-trigger:rotate-180"
						/>
					</span>
				</AccordionTrigger>
				{/* No extra padding on AccordionContent itself: the shared
				primitive already pads horizontally (px-4) and vertically
				(inner pb-4) — a second layer there produced a double
				indent/double bottom gap vs the header. The RadioGroup adds
				ONE deliberate px-2 so option text and right-aligned radios
				sit at 24px insets — balanced breathing room, never touching
				the container edges. The hairline separates options from
				header. Rows keep the radio FIRST in DOM (Radix
				roving-tabindex/reading order) and push it to the visual far
				end via ms-auto, aligning its inset with the header chevron. */}
				<AccordionContent>
					<div className="border-t border-border/5 pt-4">
						<RadioGroup
							value={preset}
							onValueChange={(v) => onPresetChange(v as AudioPreset)}
							className="gap-1 px-4"
							data-testid="mic-preset-radio-group"
						>
							{presetOptions.map((option) => (
								// biome-ignore lint/a11y/noStaticElementInteractions: the nested RadioGroupItem is the accessible control (role=radio); the row click is pointer convenience.
								// biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation goes through the focused radio itself (Space/arrows via Radix); a keydown mirror here would double-fire the selection.
								<div
									key={option.value}
									className={cn(
										"flex items-center gap-3 rounded-lg p-2 min-h-9 cursor-pointer transition-colors hover:bg-foreground/5",
										option.value === preset && "bg-foreground/5",
									)}
									data-testid={`mic-preset-option-${option.value}`}
									onClick={makeOptionClickHandler(
										preset,
										onPresetChange,
										option.value,
									)}
								>
									{/* Explicit aria-label keeps the radio's accessible
								    name to the preset LABEL — an implicit-label
								    fallback would concatenate the whole row text.
								    The radio stays FIRST in DOM (Radix roving
								    tabindex + screen-reader reading order hit the
								    control before the descriptive text) and is
								    moved to the visual far end via order-last +
								    ms-auto, aligning its inset with the header "+"
								    glyph. */}
									<RadioGroupItem
										value={option.value}
										aria-label={option.label}
										className="order-last ms-auto"
									/>
									{/* Title + its own info trigger form the left
								    content group; the info icon sits immediately
								    after the title (Settings pattern), never at the
								    far end and never inside the radio's hit area.
								    The inline span's stopPropagation (owned by
								    InfoTooltip) keeps clicks/keys off the row
								    handler. */}
									<span className="flex items-center gap-1.5 min-w-0">
										<span className="text-sm font-medium text-(--text-primary) truncate">
											{option.label}
										</span>
										<InfoTooltip
											text={option.description}
											contextLabel={option.label}
										/>
									</span>
								</div>
							))}
						</RadioGroup>
					</div>

					{isCustom && (
						<>
							<button
								type="button"
								className="mt-2 flex w-full items-center justify-between rounded-lg border border-border/5 px-3 py-2.5 text-xs font-medium text-(--text-primary) hover:bg-foreground/5 transition-colors cursor-pointer"
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
