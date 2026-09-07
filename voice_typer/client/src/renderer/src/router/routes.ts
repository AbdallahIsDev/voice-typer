import type { Page } from "../types/ipc";

/**
 * Single data-driven route table for the React renderer.
 *
 * Previously the page registry was duplicated in four places — the
 * `Page` union in `types/ipc.ts`, the `KNOWN_PAGES` Set in
 * `useNavigation.ts`, the `renderPage()` switch in `App.tsx`, and the
 * `pageMap` for the navigate event in `App.tsx` — and had already
 * drifted: the navigate `pageMap` was missing `onboarding`, so a
 * backend `navigate` event with `path: "onboarding"` hit the else
 * branch and logged a spurious warning instead of routing the user.
 *
 * This module is the runtime source of truth for the route table. The
 * `Page` union in `types/ipc.ts` remains the type-level source of
 * truth (it's how TypeScript narrows page values across the codebase);
 * this module mirrors that union as a runtime `Record` so other modules
 * can validate strings, look up routes, and iterate over the set of
 * known pages without re-listing them. Because `ROUTES` is typed as
 * `Record<Page, RouteDef>`, the compiler will flag any entry that
 * drifts from the `Page` union — closing the drift loophole.
 *
 * To add a new page:
 *   1. Add the literal to the `Page` union in `types/ipc.ts`.
 *   2. Add a `RouteDef` entry to `ROUTES` below (the compiler will
 *      error if you forget — `Record<Page, RouteDef>` requires every
 *      `Page` literal to have an entry).
 *   3. Add a `case` to `PageSwitch`'s switch in `router/PageSwitch.tsx`
 *      and, if the page gets its own chunk, an entry to the shared
 *      `PAGE_LOADERS` registry in `router/pageLoaders.ts` (component
 *      wiring — legitimate routing logic, not registry duplication).
 */
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
	// "settings" is the Settings HUB page — a real destination (one card
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

/**
 * Type guard: narrows an unknown/string value to `Page`. Accepts
 * `unknown` so callers can validate untrusted JSON (e.g. localStorage
 * payloads or IPC events) without an extra `typeof` check at the
 * call site. The `typeof path === "string"` short-circuit keeps the
 * `path in ROUTES` lookup safe for non-string inputs.
 */
export function isKnownPage(path: unknown): path is Page {
	return typeof path === "string" && path in ROUTES;
}
