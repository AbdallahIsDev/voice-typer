// Locale constants, labels, and browser-locale detection.
//
//Internationalization support.
// Supported locales: Arabic (ar), German (de), English (en), Russian (ru), Spanish (es), French (fr),
// Chinese/Mandarin (zh), Hindi (hi).
// Adding a new language requires:
//   1. Create a new JSON file in translations/ (e.g., translations/ar.json)
//   2. Add the locale to SUPPORTED_LOCALES below
//   3. Import and register it via registerTranslations() (see store.ts).
//
//when no locale is saved in localStorage, the user's preferred
// browser/OS language (navigator.languages) is matched against
// SUPPORTED_LOCALES so first-run users see their language automatically.

/**
 * Locales shipped with Voice Typer. The order here is the order shown in
 * the Settings → UI language dropdown.
 */
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

//export the list of supported locales for the UI language selector.
export { SUPPORTED_LOCALES };

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

//detect the user's preferred browser/OS language and match it
// against SUPPORTED_LOCALES. Returns the matched locale or "en" as the
// fallback. Used only on first run (when no locale is saved in
// localStorage).
//
// We consider both the full tag (e.g. "pt-BR") and the primary subtag
// (e.g. "pt") so a user with browser language "zh-CN" still matches our
// "zh" locale. We also normalise casing and ignore tags we don't ship.
export function detectBrowserLocale(): Locale {
	try {
		if (typeof navigator === "undefined") return "en";
		const candidates = navigator.languages?.length
			? navigator.languages
			: [navigator.language];
		for (const raw of candidates) {
			if (!raw) continue;
			const lower = raw.toLowerCase();
			// Exact match (e.g. "en", "ar").
			const exact = (SUPPORTED_LOCALES as readonly string[]).find(
				(loc) => loc === lower,
			);
			if (exact) return exact as Locale;
			// Primary subtag match (e.g. "zh-CN" → "zh").
			const primary = lower.split("-")[0];
			const partial = (SUPPORTED_LOCALES as readonly string[]).find(
				(loc) => loc === primary,
			);
			if (partial) return partial as Locale;
		}
	} catch (e) {
		// navigator may be unavailable (SSR, sandboxed). Fall through to "en".
		console.warn("[i18n] detectBrowserLocale failed:", e);
	}
	return "en";
}
