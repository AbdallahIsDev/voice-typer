// i18n infrastructure for Voice Typer.
// UX-015: Internationalization support.
// Supported locales: Arabic (ar), German (de), English (en), Russian (ru), Spanish (es), French (fr),
// Chinese/Mandarin (zh), Hindi (hi).
// Adding a new language requires:
//   1. Create a new JSON file in translations/ (e.g., translations/ar.json)
//   2. Add the locale to SUPPORTED_LOCALES below
//   3. Import and register it in the translations map below
//
// The t() function returns the translated string for a dot-separated key.
// If the key is not found, it falls back to English, then returns the key itself.

import { useSyncExternalStore } from "react";
import ar from "./translations/ar.json";
import de from "./translations/de.json";
import en from "./translations/en.json";
import es from "./translations/es.json";
import fr from "./translations/fr.json";
import hi from "./translations/hi.json";
import ru from "./translations/ru.json";
import zh from "./translations/zh.json";

type TranslationDict = Record<string, unknown>;

const SUPPORTED_LOCALES = [
	"ar",
	"de",
	"en",
	"ru",
	"es",
	"fr",
	"zh",
	"hi",
] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

// UX-015: export the list of supported locales for the UI language selector.
export { SUPPORTED_LOCALES };

// F-4: RTL locales (Arabic). When the current locale is RTL, the
// document direction is set to "rtl" so the entire UI flips horizontally.
const RTL_LOCALES = new Set<Locale>(["ar"]);

/**
 * Returns true if the given locale is a right-to-left language.
 */
export function isRtlLocale(locale: Locale): boolean {
	return RTL_LOCALES.has(locale);
}

// Human-readable labels for each locale (used in the Settings dropdown).
const LOCALE_LABELS: Record<Locale, string> = {
	ar: "العربية",
	de: "Deutsch",
	en: "English",
	es: "Español",
	fr: "Français",
	ru: "Русский",
	zh: "中文",
	hi: "हिन्दी",
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

// HIGH-32 / I18N-4: apply the RTL ``dir`` attribute on initial module
// load.  ``setLocale()`` (below) sets ``document.documentElement.dir``
// whenever the locale changes at runtime, but the module-load restore
// path above only sets ``_currentLocale`` in memory — it never touched
// the DOM.  So a user with Arabic saved who reloaded the app saw
// ``_currentLocale === "ar"`` but ``document.documentElement.dir === "ltr"``
// (the browser default) until they manually re-selected Arabic in
// Settings.  Setting the dir here at module init closes the gap.
try {
	if (typeof document !== "undefined") {
		document.documentElement.dir = RTL_LOCALES.has(_currentLocale)
			? "rtl"
			: "ltr";
		// CR-44: also set ``lang`` so screen readers pronounce content in
		// the user-selected UI locale (not the browser default).
		document.documentElement.lang = _currentLocale;
	}
} catch {
	// document may be unavailable in some contexts (SSR, tests)
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

// Register French translations
_translations.set("fr", flatten(fr as TranslationDict));

// Register Chinese (Mandarin) translations
_translations.set("zh", flatten(zh as TranslationDict));

// Register Hindi translations
_translations.set("hi", flatten(hi as TranslationDict));

// Register Arabic translations
_translations.set("ar", flatten(ar as TranslationDict));

// Register Russian translations
_translations.set("ru", flatten(ru as TranslationDict));

// Register German translations
_translations.set("de", flatten(de as TranslationDict));

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

// ── Locale change subscription (b-review Finding 3) ──────────────
// `t()` is a plain function with no React subscription, so switching
// the locale used to require a full `window.location.reload()` to
// repaint every component. We now keep a subscriber set and notify it
// from `setLocale`, letting the `useT()` hook (useSyncExternalStore)
// re-render subscribed components in place.
const _localeSubscribers: Set<() => void> = new Set();

/**
 * Subscribe to locale changes. Returns an unsubscribe function.
 * Used by the {@link useT} hook so React components re-render with the
 * new locale instead of requiring a page reload.
 */
export function subscribeLocale(cb: () => void): () => void {
	if (typeof cb !== "function") return () => {};
	_localeSubscribers.add(cb);
	return () => {
		_localeSubscribers.delete(cb);
	};
}

/** Snapshot of the current locale — used as `getSnapshot` for `useSyncExternalStore`. */
export function getLocaleSnapshot(): Locale {
	return _currentLocale;
}

/**
 * React hook that subscribes the calling component to locale changes and
 * returns the `t` translate function. Calling `useT()` makes the component
 * re-render whenever {@link setLocale} is invoked, so `t(...)` resolves
 * against the current locale with no full-page reload.
 */
export function useT(): typeof t {
	useSyncExternalStore(subscribeLocale, getLocaleSnapshot, getLocaleSnapshot);
	return t;
}

/**
 * Set the current locale and update the document text direction.
 *
 * F-4: When switching to an RTL locale (Arabic), sets
 * ``document.documentElement.dir = "rtl"`` so the entire UI flips
 * horizontally. Falls back to "ltr" for all other locales.
 */
export function setLocale(locale: Locale): void {
	let next: Locale = locale;
	if (!SUPPORTED_LOCALES.includes(locale)) {
		console.warn(`[i18n] Unsupported locale: ${locale}. Falling back to 'en'.`);
		next = "en";
	}
	_currentLocale = next;

	// F-4: Update document direction for RTL support.
	try {
		if (typeof document !== "undefined") {
			document.documentElement.dir = RTL_LOCALES.has(next) ? "rtl" : "ltr";
			// CR-44: also set ``lang`` so screen readers pronounce content
			// in the user-selected UI locale (not the browser default).
			document.documentElement.lang = next;
		}
	} catch {
		// SSR environments may not have document
	}

	// F-3: persist the choice so it survives restarts. Previously the
	// caller did this and relied on a full reload to re-read it.
	try {
		if (typeof localStorage !== "undefined") {
			localStorage.setItem("voice-typer-ui-locale", next);
		}
	} catch {
		// localStorage may be unavailable in some contexts
	}

	// F-3: notify subscribers (the useT hook) so every subscribed
	// component re-renders with the new locale instead of requiring a
	// page reload.
	for (const cb of _localeSubscribers) {
		try {
			cb();
		} catch {
			// a misbehaving subscriber must not break locale switching
		}
	}
}

/**
 * Translate a key to the current locale's string.
 * Falls back to English, then to the raw key if not found.
 *
 * Supports optional `{placeholder}` interpolation: if `params` is provided,
 * each `{key}` in the translated string is replaced with the corresponding
 * value from `params`.
 *
 * @param key - Dot-separated translation key (e.g., "app.name")
 * @param params - Optional interpolation params (e.g., `{ key: "Esc" }`)
 * @returns The translated string
 */
export function t(key: string, params?: Record<string, string>): string {
	let result: string;
	// Try current locale first
	const currentMap = _translations.get(_currentLocale);
	if (currentMap?.has(key)) {
		result = currentMap.get(key) ?? key;
	} else {
		// Fall back to English
		const enMap = _translations.get("en");
		if (enMap?.has(key)) {
			result = enMap.get(key) ?? key;
		} else {
			// Last resort: return the key itself
			result = key;
		}
	}
	// Interpolate {placeholder} values
	if (params) {
		for (const [k, v] of Object.entries(params)) {
			result = result.replace(new RegExp(`\\{${k}\\}`, "g"), v);
		}
	}
	return result;
}
