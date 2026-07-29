// i18n infrastructure for Voice Typer.
//
// ──────────────────────────────────────────────────────────────────
// DR-31: this 745-LOC monolith has been split into a focused i18n/
// package. This file is now a THIN RE-EXPORT so existing consumers
// (which import from `@/i18n/i18n`) keep working without code changes.
// New consumers can import directly from `@/i18n` (which resolves to
// `./i18n/index.ts`).
//
// Module split:
//   - locale.ts    — SUPPORTED_LOCALES, Locale type, LOCALE_LABELS,
//                     getLocaleLabel, detectBrowserLocale
//   - rtl.ts       — RTL_LOCALES, isRtlLocale (DR-9 1-N Finding 7)
//   - store.ts     — translation state, flatten, registerTranslations,
//                     ensureLocaleLoaded, getLocale, setLocale
//   - translate.ts — t, tChoice, interpRegex cache, PluralRules cache
//                     (DR-9 1-N Finding 6: dead PluralRules stub removed)
//   - hooks.ts     — useT, useTChoice, subscriber set,
//                     getLocaleSnapshot, subscribeLocale
//   - push.ts      — trayLabelsForLocale, pushLocaleToMainProcess,
//                     pushLocaleToPythonBackend
//   - index.ts     — public-surface re-exports + initI18n() (DR-9
//                     1-N Finding 4: explicit init replaces module-load
//                     side effects; auto-called on first import for
//                     backwards compat)
// ──────────────────────────────────────────────────────────────────
//
// UX-015: Internationalization support.
// Supported locales: Arabic (ar), German (de), English (en), Russian (ru), Spanish (es), French (fr),
// Chinese/Mandarin (zh), Hindi (hi).
// Adding a new language requires:
//   1. Create a new JSON file in translations/ (e.g., translations/ar.json)
//   2. Add the locale to SUPPORTED_LOCALES (see locale.ts)
//   3. Register it via registerTranslations() (see store.ts) — non-English
//      locales are dynamically imported via ensureLocaleLoaded() (ER-65).
//
// The t() function returns the translated string for a dot-separated key.
// If the key is not found, it falls back to English, then returns the key itself.
//
// PVT-082: tChoice() provides ICU-style pluralization on top of t().
// PVT-083: when no locale is saved in localStorage, the user's preferred
// browser/OS language (navigator.languages) is matched against
// SUPPORTED_LOCALES so first-run users see their language automatically.

export * from "./index";
