// settingsSections — the canonical registry of Settings section pages.
//
// The Settings surface is a HUB + nested section pages: the hub
// (the `settings` Page literal) renders ONE card whose rows are the
// sections below; each row navigates to its section page, which renders
// only that domain's cards. This replaces the former 4-tab model
// (General / AI & Audio / Appearance / Privacy) where up to five
// unrelated domains stacked on a single sub-page.
//
// This module is the SINGLE SOURCE OF TRUTH for the section set:
//   - the hub card rows (title, description, icon, order),
//   - `isSettingsSectionPage` (Sidebar active-state, GlobalSearchBar
//     placeholder, prefetch),
//   - the search label sets (see `settingsTabLabels.ts`, keyed by
//     `SettingsSectionPage`).
//
// The `Page` union in `types/ipc/enums.ts` must stay in lockstep: every
// literal below is a `Page`, and `Record`-typing keeps the compiler
// honest if either side drifts.

import {
	AiBrain03Icon,
	CpuIcon,
	EyeIcon,
	KeyboardIcon,
	PaintBoardIcon,
	Shield01Icon,
	SlidersHorizontalIcon,
	TextIcon,
	VolumeHighIcon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import type { Page } from "@/types/ipc";

/** Every Settings section page, in hub-card (and sidebar-history) order. */
export const SETTINGS_SECTION_PAGES = [
	"settingsGeneral",
	"settingsOverlay",
	"settingsHotkeys",
	"settingsTranscription",
	"settingsAI",
	"settingsAudio",
	"settingsAppearance",
	"settingsPrivacy",
	"settingsAdvanced",
] as const;

export type SettingsSectionPage = (typeof SETTINGS_SECTION_PAGES)[number];

export interface SettingsSectionDef {
	page: SettingsSectionPage;
	/** i18n key for the row title — the SAME key the nested page's
	 *  section card renders as its `<SettingsSection title>`, so the hub
	 *  label and the destination heading can never drift. */
	titleKey: string;
	/** i18n key for the row's muted description line. */
	descriptionKey: string;
	icon: IconSvgElement;
}

/** Registry entry shape — the page IS the key, so it's not repeated. */
type SettingsSectionDefData = Omit<SettingsSectionDef, "page">;

/**
 * The section registry, keyed BY page literal — the `Record` type makes
 * a missing/extra/renamed section page a compile error, so this module
 * and the `Page` union cannot drift. Key insertion order IS the hub-card
 * order; the array exports below derive from it.
 */
const SETTINGS_SECTION_DEFS: Record<
	SettingsSectionPage,
	SettingsSectionDefData
> = {
	settingsGeneral: {
		titleKey: "settings.general",
		descriptionKey: "settings.generalDescription",
		icon: SlidersHorizontalIcon,
	},
	settingsOverlay: {
		titleKey: "settings.overlay",
		descriptionKey: "settings.overlayDescription",
		icon: EyeIcon,
	},
	settingsHotkeys: {
		titleKey: "settings.hotkeySection.recordingTitle",
		descriptionKey: "settings.hotkeySection.recordingDescription",
		icon: KeyboardIcon,
	},
	settingsTranscription: {
		titleKey: "settings.postProcessing",
		descriptionKey: "settings.postProcessingDescription",
		icon: TextIcon,
	},
	settingsAI: {
		titleKey: "settings.aiEnhancement.title",
		descriptionKey: "settings.hub.aiDescription",
		icon: AiBrain03Icon,
	},
	settingsAudio: {
		titleKey: "settings.audioEnhancement.title",
		descriptionKey: "settings.audioEnhancement.description",
		icon: VolumeHighIcon,
	},
	settingsAppearance: {
		titleKey: "settings.appearance.title",
		descriptionKey: "settings.appearance.description",
		icon: PaintBoardIcon,
	},
	settingsPrivacy: {
		titleKey: "settings.privacy.privacyTitle",
		descriptionKey: "settings.privacy.privacyDescription",
		icon: Shield01Icon,
	},
	settingsAdvanced: {
		titleKey: "settings.hub.advancedTitle",
		descriptionKey: "settings.hub.advancedDescription",
		icon: CpuIcon,
	},
};

/** The registry as an ordered list (hub rows iterate this). */
export const SETTINGS_SECTIONS: readonly SettingsSectionDef[] =
	SETTINGS_SECTION_PAGES.map((page) => ({
		page,
		...SETTINGS_SECTION_DEFS[page],
	}));

/** Section page → its title i18n key. Consumers that need a section's
 *  display title WITHOUT its description/icon (e.g. App.tsx's
 *  document.title, cross-section search-result group headers) use this
 *  so they can never drift from the registry. */
export const SECTION_TITLE_BY_PAGE: Record<SettingsSectionPage, string> =
	Object.fromEntries(
		SETTINGS_SECTION_PAGES.map((page) => [
			page,
			SETTINGS_SECTION_DEFS[page].titleKey,
		]),
	) as Record<SettingsSectionPage, string>;

/** Type guard: is `page` one of the Settings section pages? */
export function isSettingsSectionPage(page: Page): page is SettingsSectionPage {
	return (SETTINGS_SECTION_PAGES as readonly string[]).includes(page);
}

/** Is `page` any Settings surface (hub or a section page)? */
export function isSettingsSurface(
	page: Page,
): page is SettingsSectionPage | "settings" {
	return page === "settings" || isSettingsSectionPage(page);
}
