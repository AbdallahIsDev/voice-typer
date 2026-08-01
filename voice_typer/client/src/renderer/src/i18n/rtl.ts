// RTL (right-to-left) locale helpers.
//
// F-4: RTL locales (Arabic). When the current locale is RTL, the
// document direction is set to "rtl" so the entire UI flips horizontally.
//
//sub-finding (1-N Finding 7): extracted from the old i18n.ts
// monolith so layout components that only need to query the current
// writing direction can import a tiny module instead of pulling the
// whole i18n surface (translation tables, pluralization caches, etc.).
//
// The i18n package's `index.ts` re-exports `isRtlLocale` and
// `RTL_LOCALES` for backwards compatibility with existing callers that
// import from `@/i18n/i18n`.

import type { Locale } from "./locale";

/**
 * Set of locales rendered right-to-left. Currently only Arabic.
 *
 * Exposed so layout components can read the same source-of-truth the
 * i18n module uses when deciding whether to set `dir="rtl"`.
 */
export const RTL_LOCALES: Set<Locale> = new Set<Locale>(["ar"]);

/**
 * Returns true if the given locale is a right-to-left language.
 */
export function isRtlLocale(locale: Locale): boolean {
	return RTL_LOCALES.has(locale);
}
