// i18n infrastructure for Voice Typer.
// UX-015: Minimal internationalization support.
// Currently only English is supported; adding a new language requires:
//   1. Create a new JSON file in translations/ (e.g., translations/es.json)
//   2. Add the locale to SUPPORTED_LOCALES below
//   3. Import and register it in the translations map
//
// The t() function returns the translated string for a dot-separated key.
// If the key is not found, it falls back to English, then returns the key itself.

import en from './translations/en.json'

type TranslationDict = Record<string, unknown>

const SUPPORTED_LOCALES = ['en'] as const
export type Locale = (typeof SUPPORTED_LOCALES)[number]

// Current locale — defaults to 'en'. Can be changed via setLocale().
let _currentLocale: Locale = 'en'

// Translation map: locale -> flat key-value pairs
const _translations: Map<Locale, Map<string, string>> = new Map()

/**
 * Flatten a nested JSON object into dot-separated keys.
 * e.g. { "app": { "name": "Voice Typer" } } → { "app.name": "Voice Typer" }
 */
function flatten(obj: TranslationDict, prefix = ''): Map<string, string> {
  const result = new Map<string, string>()
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}.${key}` : key
    if (typeof value === 'object' && value !== null) {
      const nested = flatten(value as TranslationDict, fullKey)
      for (const [k, v] of nested) {
        result.set(k, v)
      }
    } else if (typeof value === 'string') {
      result.set(fullKey, value)
    }
  }
  return result
}

// Register English translations
_translations.set('en', flatten(en as TranslationDict))

/**
 * Register translations for a locale.
 */
export function registerTranslations(locale: Locale, data: TranslationDict): void {
  _translations.set(locale, flatten(data))
}

/**
 * Get the current locale.
 */
export function getLocale(): Locale {
  return _currentLocale
}

/**
 * Set the current locale.
 */
export function setLocale(locale: Locale): void {
  if (!SUPPORTED_LOCALES.includes(locale)) {
    console.warn(`[i18n] Unsupported locale: ${locale}. Falling back to 'en'.`)
    _currentLocale = 'en'
    return
  }
  _currentLocale = locale
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
  const currentMap = _translations.get(_currentLocale)
  if (currentMap?.has(key)) {
    return currentMap.get(key)!
  }
  // Fall back to English
  const enMap = _translations.get('en')
  if (enMap?.has(key)) {
    return enMap.get(key)!
  }
  // Last resort: return the key itself
  return key
}
