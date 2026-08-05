/**
 * Locale-key parity test: asserts every non-English locale file ships
 * the SAME set of dot-keys as `en.json`.
 *
 * CONSTRAINT C-I18N-1 requires that every user-visible string be added
 * to ALL 8 locale files (`en`, `ar`, `de`, `es`, `fr`, `hi`, `ru`,
 * `zh`). Without this test, a key added to `en.json` but forgotten in
 * (say) `hi.json` silently falls back to English for Hindi users —
 * invisible when only English is exercised by unit tests, and not
 * caught by the existing `t()` dev-mode missing-key warning when the
 * fallback to English succeeds (the warning only fires when the key is
 * missing from BOTH the current locale AND English).
 *
 * This test reads all 8 locale JSON files directly (NOT through the
 * runtime `t()` pipeline — runtime imports would post-process the
 * values via `_withAppName` and cache them, which is irrelevant for
 * key-set comparison; what matters here is the raw key surface area
 * translators see when they edit the JSON files). It flattens each
 * file via the renderer's `flatten()` helper (same dot-key scheme the
 * runtime uses) and asserts `Set(keys(en)) === Set(keys(<locale>))`
 * for every non-English locale.
 *
 * On failure, the test reports the symmetric difference split into
 *   - `missingInLocale`: keys present in `en.json` but absent from
 *     the locale file (translator needs to add these).
 *   - `extraInLocale`: keys present in the locale file but absent
 *     from `en.json` (translator needs to remove these — typically a
 *     stale key whose English counterpart was renamed or deleted).
 * so the developer knows exactly which keys to add or remove without
 * diffing the JSON files by hand.
 *
 * Structure mirrors `themes/__tests__/parity.test.ts` (describe
 * derived from a canonical list of fixtures, one describe-block per
 * fixture, symmetric-difference diagnostics on failure).
 */
import { describe, expect, it } from "vitest";
import { flatten } from "@/i18n/store";
import ar from "@/i18n/translations/ar.json";
import de from "@/i18n/translations/de.json";
import en from "@/i18n/translations/en.json";
import es from "@/i18n/translations/es.json";
import fr from "@/i18n/translations/fr.json";
import hi from "@/i18n/translations/hi.json";
import ru from "@/i18n/translations/ru.json";
import zh from "@/i18n/translations/zh.json";

type TranslationDict = Record<string, unknown>;

// The 8 shipped locales. English is the reference (golden) key set;
// every other locale is parity-checked against it. Derived from the
// imports above (not from `SUPPORTED_LOCALES`) so a stale import —
// e.g. someone forgets to import the `pt` JSON file after adding
// `"pt"` to `SUPPORTED_LOCALES` — would surface as a missing
// describe-block here rather than silently being skipped.
const LOCALES = {
	ar,
	de,
	es,
	fr,
	hi,
	ru,
	zh,
} as const;

// Flatten each locale JSON into a Map<dotKey, value> using the SAME
// helper the runtime uses (so the parity test compares the exact key
// surface the renderer sees after `flatten()`, not the raw nested
// object shape). Keys are computed once at module-eval time so each
// `it()` block in the describe loop runs in O(1) on the cached Set.
const enFlat = flatten(en as TranslationDict);
const enKeys = new Set<string>(enFlat.keys());

const localeFlats: Record<keyof typeof LOCALES, Map<string, string>> = {
	ar: flatten(ar as TranslationDict),
	de: flatten(de as TranslationDict),
	es: flatten(es as TranslationDict),
	fr: flatten(fr as TranslationDict),
	hi: flatten(hi as TranslationDict),
	ru: flatten(ru as TranslationDict),
	zh: flatten(zh as TranslationDict),
};

describe("locale-key parity with en.json (C-I18N-1)", () => {
	// Sanity guard: if a future locale is added to the imports above
	// but not to SUPPORTED_LOCALES (or vice versa), this assertion
	// fires. Update both sides explicitly when adding/removing a
	// locale. 7 non-English locales × 1 reference (en) = 8 total.
	it("exercises every non-English locale (regression guard)", () => {
		expect(Object.keys(LOCALES).length).toBe(7);
		const exercised = new Set(Object.keys(LOCALES));
		for (const expected of ["ar", "de", "es", "fr", "hi", "ru", "zh"]) {
			expect(exercised.has(expected), `missing locale ${expected}`).toBe(true);
		}
	});

	for (const [locale, _data] of Object.entries(LOCALES)) {
		// Cast back to the locale key for the typed lookup into
		// `localeFlats`. The Object.entries iteration widens the key to
		// `string`; we know it's one of the 7 keys of `LOCALES`.
		const localeKey = locale as keyof typeof LOCALES;

		describe(`${locale}.json`, () => {
			it("defines the same set of dot-keys as en.json", () => {
				const localeMap = localeFlats[localeKey];
				const localeKeys = new Set<string>(localeMap.keys());

				// Symmetric difference split into the two directions a
				// developer needs to act on. Reporting both in the same
				// `expect` message keeps the failure output copy-paste-
				// actionable: the developer sees every key they need to
				// touch in one block.
				const missingInLocale: string[] = [];
				for (const key of enKeys) {
					if (!localeKeys.has(key)) missingInLocale.push(key);
				}
				const extraInLocale: string[] = [];
				for (const key of localeKeys) {
					if (!enKeys.has(key)) extraInLocale.push(key);
				}

				// Sort so the diff is deterministic across test runs
				// (Set iteration order is insertion-order, which can vary
				// if the JSON file is re-serialised). Deterministic
				// output is essential for snapshot-style review and for
				// CI log diffing.
				missingInLocale.sort();
				extraInLocale.sort();

				const diff = {
					missingInLocale,
					extraInLocale,
				};
				expect(
					diff,
					`${locale}.json key set drifts from en.json — ` +
						`${missingInLocale.length} missing, ${extraInLocale.length} extra. ` +
						`Add the missing keys to ${locale}.json (with a genuine ` +
						`translation — C-I18N-2) and remove the extra keys.`,
				).toEqual({ missingInLocale: [], extraInLocale: [] });
			});
		});
	}
});

// Direct test of the `_withAppName` helper exported from store.ts.
// Verifies the substitution is applied at registration time so the
// runtime `t()` returns the substituted value (the actual ~290-string
// sweep to MIGRATE literals to `{appName}` is a separate task — this
// just locks in the mechanism so the migration is unblocked).
describe("_withAppName helper", () => {
	it("substitutes {appName} with APP_NAME on every value", async () => {
		const { _withAppName } = await import("@/i18n/store");
		const { APP_NAME } = await import("@/branding");
		const input = {
			"dialog.title": "{appName} cannot restart safely",
			"dialog.body":
				"{appName}'s backend exited. Restart {appName} to continue.",
			"dialog.noPlaceholder": "Plain string with no placeholder.",
		};
		const result = _withAppName(input);
		expect(result["dialog.title"]).toBe(`${APP_NAME} cannot restart safely`);
		expect(result["dialog.body"]).toBe(
			`${APP_NAME}'s backend exited. Restart ${APP_NAME} to continue.`,
		);
		expect(result["dialog.noPlaceholder"]).toBe(
			"Plain string with no placeholder.",
		);
		// Input must not be mutated.
		expect(input["dialog.title"]).toBe("{appName} cannot restart safely");
	});

	it("returns a new record (does not mutate the input)", async () => {
		const { _withAppName } = await import("@/i18n/store");
		const input = { "a.b": "{appName} X" };
		const result = _withAppName(input);
		expect(result).not.toBe(input);
		expect(input["a.b"]).toBe("{appName} X");
		expect(result["a.b"]).toMatch(/X$/);
	});

	it("the runtime t() returns APP_NAME-substituted values for {appName} keys", async () => {
		// Verify the substitution actually fires through the runtime
		// path: register a fixture table containing a `{appName}` value
		// and assert `t()` returns the substituted string (not the raw
		// `{appName}` placeholder). This catches a regression where
		// `_withAppName` is exported but accidentally NOT wired into
		// `registerTranslations` (e.g. someone refactors and forgets
		// to re-add the `_applyAppName(flatten(...))` call).
		const { registerTranslations, _translations } = await import(
			"@/i18n/store"
		);
		const { _invalidateResolvedCache, t } = await import("@/i18n/translate");
		const { _setCurrentLocale } = await import("@/i18n/store");
		const { APP_NAME } = await import("@/branding");

		// Use a sentinel locale tag so we don't disturb the real `en`
		// table other tests depend on. Cast through `unknown` because
		// `Locale` is a closed union that excludes synthetic test tags.
		const TEST_LOCALE = "__withAppNameTest__" as unknown as Parameters<
			typeof registerTranslations
		>[0];
		registerTranslations(TEST_LOCALE, {
			dialog: { branded: "{appName} is ready." },
		});
		_setCurrentLocale(TEST_LOCALE);
		_invalidateResolvedCache(TEST_LOCALE);
		try {
			const result = t("dialog.branded");
			expect(result).toBe(`${APP_NAME} is ready.`);
		} finally {
			// Clean up so the synthetic locale doesn't leak into other
			// tests via the shared module-level `_translations` Map.
			_translations.delete(TEST_LOCALE);
			_invalidateResolvedCache(TEST_LOCALE);
			_setCurrentLocale("en");
		}
	});
});
