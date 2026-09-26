import type { Page } from "../types/ipc";

export interface RouteDef {
	page: Page;
}

export const ROUTES: Record<Page, RouteDef> = {
	home: { page: "home" },
	history: { page: "history" },
	microphone: { page: "microphone" },
	models: { page: "models" },
	templates: { page: "templates" },
	vocabulary: { page: "vocabulary" },
	media: { page: "media" },
	// "settings" is the Settings HUB page, a real destination (one card
	// whose rows open the section pages below). Existing call sites
	// (Ctrl+, shortcut, tray menu, Python `navigate {path: "/settings"}`
	// IPC event) land directly on the hub; the hub rows navigate to the
	// focused section pages. The section-page literal set is owned by
	// `components/settings/settingsSections.ts` and must stay in
	// lockstep with the `Page` union.
	settings: { page: "settings" },
	settingsGeneral: { page: "settingsGeneral" },
	settingsOverlay: { page: "settingsOverlay" },
	settingsHotkeys: { page: "settingsHotkeys" },
	settingsTranscription: { page: "settingsTranscription" },
	settingsAI: { page: "settingsAI" },
	settingsAudio: { page: "settingsAudio" },
	settingsAppearance: { page: "settingsAppearance" },
	settingsPrivacy: { page: "settingsPrivacy" },
	settingsAdvanced: { page: "settingsAdvanced" },
	analytics: { page: "analytics" },
	aboutAndPrivacy: { page: "aboutAndPrivacy" },
	onboarding: { page: "onboarding" },
};

export function isKnownPage(path: unknown): path is Page {
	return typeof path === "string" && path in ROUTES;
}
