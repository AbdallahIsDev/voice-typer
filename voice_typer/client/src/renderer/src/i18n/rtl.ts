// RTL (right-to-left) locale helpers.
// F-4: RTL locales (Arabic). When the current locale is RTL, the
// document direction is set to "rtl" so the entire UI flips horizontally.
// writing direction can import a tiny module instead of pulling the
// whole i18n surface (translation tables, pluralization caches, etc.).
// The i18n package's `index.ts` re-exports `isRtlLocale` and
// `RTL_LOCALES` for backwards compatibility with existing callers that
// import from `@/i18n/i18n`.

import type { Locale } from "./locale";

export const RTL_LOCALES: Set<Locale> = new Set<Locale>(["ar"]);

export function isRtlLocale(locale: Locale): boolean {
	return RTL_LOCALES.has(locale);
}
