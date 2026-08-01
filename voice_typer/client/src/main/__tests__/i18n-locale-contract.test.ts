// @vitest-environment node
/**
 *  contract test: the main-process i18n bundle must stay in sync
 * with the renderer's `SUPPORTED_LOCALES`.
 *
 * The renderer's `SUPPORTED_LOCALES` (in
 * `src/renderer/src/i18n/i18n.ts`) is the canonical list of locales the
 * user can pick in the UI language selector. The main-process
 * `MAIN_STRINGS` (in `src/main/i18n.ts`) must provide a dialog-strings
 * table for every locale in that list — otherwise the renderer can set
 * a locale that the main process silently falls back from to "en"
 * (per `setMainLocale`'s fallback), so native Electron dialogs
 * (single-instance error, critical-error crash dialog, export save-as
 * dialogs) would display in English even though the renderer is in the
 * user's chosen language.
 *
 * This test hardcodes the expected locale list (mirroring
 * `SUPPORTED_LOCALES` in `src/renderer/src/i18n/i18n.ts`). The list is
 * small (8 entries) and changes rarely — when a locale is added on the
 * renderer side, this test must be updated in lockstep. The hardcoded
 * approach avoids the cross-tsconfig-project import that would otherwise
 * be needed to read `SUPPORTED_LOCALES` from the renderer's i18n module
 * (the main-process tsconfig.node.json only includes `src/main/**`).
 */
import { describe, expect, it } from "vitest";

import { mainT, setMainLocale } from "../i18n";

// Mirror of `SUPPORTED_LOCALES` in `src/renderer/src/i18n/i18n.ts`.
// When the renderer adds a new locale, this array MUST be updated.
const EXPECTED_LOCALES = [
	"ar",
	"de",
	"en",
	"ru",
	"es",
	"fr",
	"zh",
	"hi",
] as const;

describe("AC-114: main-process i18n locales match renderer SUPPORTED_LOCALES", () => {
	it("EXPECTED_LOCALES mirrors the renderer's 8-locale SUPPORTED_LOCALES", () => {
		// Sanity: the hardcoded list isn't accidentally truncated.
		expect(EXPECTED_LOCALES.length).toBe(8);
		expect(EXPECTED_LOCALES).toContain("en");
	});

	it("every renderer locale is registered in MAIN_STRINGS", () => {
		// `setMainLocale` falls back to "en" with a console warning when
		// the locale is not in MAIN_STRINGS. We detect the fallback by
		// comparing the localized `dialog.criticalError.title` against
		// the English value — a registered non-en locale returns a
		// locale-specific title that differs from English.
		setMainLocale("en");
		const enTitle = mainT("dialog.criticalError.title");
		expect(enTitle.length).toBeGreaterThan(0);

		for (const locale of EXPECTED_LOCALES) {
			setMainLocale(locale);
			const title = mainT("dialog.criticalError.title");
			// Registered locale → lookup resolves to a real string,
			// never the raw key.
			expect(title).not.toBe("dialog.criticalError.title");
			expect(title.length).toBeGreaterThan(0);

			if (locale !== "en") {
				// A registered non-en locale must return a non-English
				// title. If setMainLocale silently fell back to "en",
				// title === enTitle and this assertion fails — surfacing
				// the missing locale JSON file.
				expect(title).not.toBe(enTitle);
			}
		}
	});

	it("setMainLocale falls back to en for an unregistered locale", () => {
		// Defensive: confirm the fallback path still works for an
		// unknown locale (so adding a renderer locale without a
		// matching main-process JSON degrades gracefully rather than
		// crashing).
		setMainLocale("xx-pirate");
		const title = mainT("dialog.criticalError.title");
		// Fallback resolves to the English title, not the raw key.
		expect(title).not.toBe("dialog.criticalError.title");
		expect(title.length).toBeGreaterThan(0);
	});

	it("every locale provides the full English key set", () => {
		// Every locale JSON must define all keys present in en.json.
		// We probe one representative key per dialog group.
		const probeKeys = [
			"dialog.criticalError.body",
			"dialog.criticalError.title",
			"dialog.export.config",
			"dialog.export.history",
			"dialog.export.templates",
			"dialog.export.vocabulary",
			"dialog.selectModelFolder.title",
			"dialog.singleInstance.message",
			"dialog.singleInstance.title",
		];
		setMainLocale("en");
		const enValues = probeKeys.map((k) => mainT(k));

		for (const locale of EXPECTED_LOCALES) {
			setMainLocale(locale);
			for (let i = 0; i < probeKeys.length; i++) {
				// noUncheckedIndexedAccess: probeKeys[i] widens to
				// `string | undefined`; the loop bound proves it exists,
				// so guard explicitly.
				const key = probeKeys[i];
				if (key === undefined) continue;
				const v = mainT(key);
				// Lookup must resolve (never returns the raw key).
				expect(v).not.toBe(key);
				expect(v.length).toBeGreaterThan(0);
				if (locale === "en") {
					expect(v).toBe(enValues[i]);
				}
			}
		}

		// Restore default locale to avoid cross-test leakage.
		setMainLocale("en");
	});
});
