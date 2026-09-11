// Shared microphone-quality audio preset options.
//
// Single source of truth for the five `audio_preset` values the backend
// accepts (defined in `voice_typer/server/audio_presets.py`, the
// preset → filter-chain mapping lives server-side per ADR 0007) and
// their i18n label/description keys. Used by BOTH live preset
// presentations:
//   - the Settings → Audio "Microphone Quality" Select
//     (components/settings/AudioSettingsSection.tsx)
//   - the Microphone page's accordion + RadioGroup selector
//     (pages/microphone/components/PresetAccordionSelector.tsx)
//
// This module holds DATA only (values + translation keys, typed against
// the `TranslationKey` union so a typo'd or missing key is a compile
// error). Each presentation resolves the keys through its own `t`
// binding (the Settings section uses the reactive `useT()` hook; the
// Microphone-page accordion uses the plain `t` import inside a
// mount-once `useMemo`), so locale reactivity stays exactly as each
// surface had it before the consolidation.
//
// The label/description keys live under `settings.audioEnhancement.preset*`
// in the locale catalogues, ONE key family shared by every surface.
import type { TranslationKey } from "@/i18n";

/** Microphone-quality preset value stored in `config.audio_preset`. */
export type AudioPreset = "auto" | "studio" | "noisy_room" | "off" | "custom";

/** One preset option: value + the i18n keys for its label and description. */
export interface AudioPresetOption {
	value: AudioPreset;
	labelKey: TranslationKey;
	descriptionKey: TranslationKey;
}

/**
 * The five microphone-quality presets in canonical display order.
 *
 * Each preset carries a *real* description (distinct from the label) so
 * the user gets an explanation of what the preset actually does, the
 * Microphone page surfaces it behind a per-row InfoTooltip, the Settings
 * surface shows it beside the Select. Descriptions resolve from the
 * `settings.audioEnhancement.preset*Description` keys.
 */
export const AUDIO_PRESET_OPTIONS: readonly AudioPresetOption[] = [
	{
		value: "auto",
		labelKey: "settings.audioEnhancement.presetAuto",
		descriptionKey: "settings.audioEnhancement.presetAutoDescription",
	},
	{
		value: "studio",
		labelKey: "settings.audioEnhancement.presetStudio",
		descriptionKey: "settings.audioEnhancement.presetStudioDescription",
	},
	{
		value: "noisy_room",
		labelKey: "settings.audioEnhancement.presetNoisyRoom",
		descriptionKey: "settings.audioEnhancement.presetNoisyRoomDescription",
	},
	{
		value: "off",
		labelKey: "settings.audioEnhancement.presetOff",
		descriptionKey: "settings.audioEnhancement.presetOffDescription",
	},
	{
		value: "custom",
		labelKey: "settings.audioEnhancement.presetCustom",
		descriptionKey: "settings.audioEnhancement.presetCustomDescription",
	},
] as const;
