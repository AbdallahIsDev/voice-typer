// i18n infrastructure for Voice Typer.
// UX-015: Internationalization support.
// Currently supports English (en) and Spanish (es) as a proof of concept.
// Adding a new language requires:
//   1. Create a new JSON file in translations/ (e.g., translations/fr.json)
//   2. Add the locale to SUPPORTED_LOCALES below
//   3. Import and register it in the translations map below
//
// The t() function returns the translated string for a dot-separated key.
// If the key is not found, it falls back to English, then returns the key itself.

import en from "./translations/en.json";
import es from "./translations/es.json";

type TranslationDict = Record<string, unknown>;

const SUPPORTED_LOCALES = ["en", "es"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

// UX-015: export the list of supported locales for the UI language selector.
export { SUPPORTED_LOCALES };

// Human-readable labels for each locale (used in the Settings dropdown).
const LOCALE_LABELS: Record<Locale, string> = {
	en: "English",
	es: "Español",
};

/**
 * Get the human-readable label for a locale.
 */
export function getLocaleLabel(locale: Locale): string {
	return LOCALE_LABELS[locale] ?? locale;
}

// Current locale — defaults to 'en'. Can be changed via setLocale().
// UX-015: restore from localStorage on module load so the user's
// language choice survives app restarts.
let _currentLocale: Locale = "en";
try {
	if (typeof localStorage !== "undefined") {
		const saved = localStorage.getItem("voice-typer-ui-locale");
		if (saved && (SUPPORTED_LOCALES as readonly string[]).includes(saved)) {
			_currentLocale = saved as Locale;
		}
	}
} catch {
	// localStorage may be unavailable in some contexts (SSR, sandboxed)
}

// Translation map: locale -> flat key-value pairs
const _translations: Map<Locale, Map<string, string>> = new Map();

/**
 * Flatten a nested JSON object into dot-separated keys.
 * e.g. { "app": { "name": "Voice Typer" } } → { "app.name": "Voice Typer" }
 */
function flatten(obj: TranslationDict, prefix = ""): Map<string, string> {
	const result = new Map<string, string>();
	for (const [key, value] of Object.entries(obj)) {
		const fullKey = prefix ? `${prefix}.${key}` : key;
		if (typeof value === "object" && value !== null) {
			const nested = flatten(value as TranslationDict, fullKey);
			for (const [k, v] of nested) {
				result.set(k, v);
			}
		} else if (typeof value === "string") {
			result.set(fullKey, value);
		}
	}
	return result;
}

// Register English translations
_translations.set("en", flatten(en as TranslationDict));

// Register Spanish translations (UX-015: proof of concept for i18n)
_translations.set("es", flatten(es as TranslationDict));

/**
 * Register translations for a locale.
 */
export function registerTranslations(
	locale: Locale,
	data: TranslationDict,
): void {
	_translations.set(locale, flatten(data));
}

/**
 * Get the current locale.
 */
export function getLocale(): Locale {
	return _currentLocale;
}

/**
 * Set the current locale.
 */
export function setLocale(locale: Locale): void {
	if (!SUPPORTED_LOCALES.includes(locale)) {
		console.warn(`[i18n] Unsupported locale: ${locale}. Falling back to 'en'.`);
		_currentLocale = "en";
		return;
	}
	_currentLocale = locale;
}

/**
 * Translate a key to the current locale's string.
 * Falls back to English, then to the raw key if not found.
 *
 * @param key - Dot-separated translation key (e.g., "app.name")
 * @returns The translated string
 */
export function t(key: string): string {
	// Try current locale first
	const currentMap = _translations.get(_currentLocale);
	if (currentMap?.has(key)) {
		return currentMap.get(key) ?? key;
	}
	// Fall back to English
	const enMap = _translations.get("en");
	if (enMap?.has(key)) {
		return enMap.get(key) ?? key;
	}
	// Last resort: return the key itself
	return key;
}
