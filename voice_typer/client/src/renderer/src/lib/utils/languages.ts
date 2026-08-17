// Shared ASR transcription-language options.
//
// Single source of truth for the language codes the backend accepts
// (`config.language`) and their i18n label keys. Used by:
//   - the Settings transcription-language select (ModelSettingsSection)
//   - the Analytics "Current Setup" language card (via formatLanguage)
//
// Dropdown option labels are translated at render time so they follow
// the user's chosen UI language. No `description` field is set on any
// entry — earlier versions shipped inconsistent per-language
// descriptions (only English and Auto-detect had them), which made the
// dropdown look broken in other languages. Descriptions were removed
// entirely for consistency.
export const LANGUAGE_OPTIONS = [
	{ value: "auto", labelKey: "settings.languageAutoDetect" },
	{ value: "en", labelKey: "settings.languageEnglish" },
	{ value: "zh", labelKey: "settings.languageChinese" },
	{ value: "es", labelKey: "settings.languageSpanish" },
	{ value: "ar", labelKey: "settings.languageArabic" },
	{ value: "fr", labelKey: "settings.languageFrench" },
	{ value: "ru", labelKey: "settings.languageRussian" },
	{ value: "pt", labelKey: "settings.languagePortuguese" },
	{ value: "de", labelKey: "settings.languageGerman" },
	{ value: "ja", labelKey: "settings.languageJapanese" },
	{ value: "ko", labelKey: "settings.languageKorean" },
	{ value: "it", labelKey: "settings.languageItalian" },
	{ value: "nl", labelKey: "settings.languageDutch" },
	{ value: "pl", labelKey: "settings.languagePolish" },
	{ value: "tr", labelKey: "settings.languageTurkish" },
	{ value: "vi", labelKey: "settings.languageVietnamese" },
	{ value: "th", labelKey: "settings.languageThai" },
	{ value: "hi", labelKey: "settings.languageHindi" },
	{ value: "id", labelKey: "settings.languageIndonesian" },
	{ value: "sv", labelKey: "settings.languageSwedish" },
	{ value: "da", labelKey: "settings.languageDanish" },
	{ value: "fi", labelKey: "settings.languageFinnish" },
	{ value: "no", labelKey: "settings.languageNorwegian" },
	{ value: "cs", labelKey: "settings.languageCzech" },
	{ value: "ro", labelKey: "settings.languageRomanian" },
	{ value: "hu", labelKey: "settings.languageHungarian" },
	{ value: "el", labelKey: "settings.languageGreek" },
	{ value: "he", labelKey: "settings.languageHebrew" },
] as const;

/** Code → i18n label-key lookup for display (formatLanguage). */
export const LANGUAGE_LABEL_KEYS: Readonly<Record<string, string>> =
	Object.fromEntries(
		LANGUAGE_OPTIONS.map(({ value, labelKey }) => [value, labelKey]),
	);
