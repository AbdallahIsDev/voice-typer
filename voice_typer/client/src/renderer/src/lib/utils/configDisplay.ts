// Display-name helpers for config values shown in the UI.
//
// The backend config stores machine values ("cuda", "cpu", "tiny",
// "en"). These helpers map them to user-facing labels so every surface
// (Analytics Current Setup, About, Home share image) renders the same
// friendly text. The internal config values are NOT changed — only the
// rendered text.
//
// - device: "cuda" → "GPU" (friendly; the config keeps "cuda" to avoid
//   a wider refactor). "cpu" → "CPU". Unknown values pass through.
// - model: capitalize the first letter ("tiny" → "Tiny") so Model /
//   Device / Language share one capitalization convention on the
//   Current Setup cards.
// - language: map the ISO code to the full localized name ("en" →
//   "English", "" → "Auto-detect") using the same i18n label keys as
//   the Settings language select (single source: lib/utils/languages).
import { t } from "@/i18n/i18n";
import { LANGUAGE_LABEL_KEYS } from "@/lib/utils/languages";

/** "cuda" → "GPU", "cpu" → "CPU" (display only; config stays "cuda"). */
export function formatDevice(device: string): string {
	if (device === "cuda") return "GPU";
	if (device === "cpu") return "CPU";
	return device;
}

/** Capitalize the first letter of a model name ("tiny" → "Tiny"). */
export function formatModel(model: string): string {
	if (!model) return "";
	return model.charAt(0).toUpperCase() + model.slice(1);
}

/**
 * Full localized language name for a config language code.
 * Empty string / "auto" → the localized "Auto-detect" label; unknown
 * codes pass through as-is.
 */
export function formatLanguage(code: string): string {
	if (!code) return t("settings.languageAutoDetect");
	const labelKey = LANGUAGE_LABEL_KEYS[code];
	return labelKey ? t(labelKey) : code;
}
