// i18n shared state container + loader + locale-switch orchestrator.
//
// This module is the single owner of the mutable i18n runtime state:
//
//   - ``_currentLocale``        — the active UI locale
//   - ``_translations``         — Map<Locale, Map<dotKey, value>>
//   - ``_localeLoadInitiated``  — Set of locales whose dynamic import
//                                  has been kicked off (dedup guard)
//   - ``_localeLoadPromises``   — Map of in-flight dynamic-import
//                                  promises (await dedup)
//
// DR-9 sub-finding (1-N Finding 4): no module-load side effects live
// here. The localStorage restore + browser-locale detection + async
// load kickoff that USED to run at module eval time has moved to
// ``initI18n()`` in ``./index.ts``. That function is auto-called on
// first import (preserving the prior behavior) AND exposed for explicit
// initialization from ``main.tsx`` / test setup.
//
// Other i18n modules import from here to access shared state. The state
// is exported as ``const`` references (Maps/Sets whose contents mutate
// but whose identity is stable) plus a small mutator for ``_currentLocale``
// (which IS reassigned on locale switch).

import { notifyLocaleSubscribers } from "./hooks";
import { type Locale, SUPPORTED_LOCALES } from "./locale";
import { pushLocaleToMainProcess, pushLocaleToPythonBackend } from "./push";
import { isRtlLocale } from "./rtl";
// DJ-95: invalidate per-(locale, key) resolved-string cache when a
// locale's translation map is replaced.
import { _invalidateResolvedCache } from "./translate";
// ER-65: ar/de/es/fr/hi/ru/zh dynamically imported via ensureLocaleLoaded()
import en from "./translations/en.json";

type TranslationDict = Record<string, unknown>;

// ── Shared mutable state ──────────────────────────────────────────

// Current locale — defaults to 'en'. ``setLocale`` / ``initI18n`` write
// to this via ``_setCurrentLocale``; every other module reads it via
// ``getLocale``.
//
// PVT-083: the initial restore-from-localStorage + browser-detect now
// lives in ``initI18n()`` (in ``./index.ts``) so the module body is
// side-effect free.
let _currentLocale: Locale = "en";

/**
 * Read the current locale.
 */
export function getLocale(): Locale {
	return _currentLocale;
}

/**
 * Internal mutator used by {@link setLocale} and {@link initI18n} to
 * update ``_currentLocale``. Other modules read via {@link getLocale}
 * — they MUST NOT mutate locale state directly.
 *
 * Exported with a leading underscore so callers know it's an internal
 * API (no consumer outside the i18n package should touch it).
 */
export function _setCurrentLocale(next: Locale): void {
	_currentLocale = next;
}

// Translation map: locale -> flat key-value pairs.
export const _translations: Map<Locale, Map<string, string>> = new Map();

// ER-65: locales whose dynamic import has already been kicked off.
// Prevents duplicate network requests when both the init-time
// auto-load AND the first ``t()`` call race for the same locale.
export const _localeLoadInitiated: Set<Locale> = new Set();

// Pending dynamic-import promises — used to deduplicate concurrent
// ``ensureLocaleLoaded`` calls for the same locale.
export const _localeLoadPromises: Map<Locale, Promise<void>> = new Map();

// ── Loader / registration ────────────────────────────────────────

/**
 * Flatten a nested JSON object into dot-separated keys.
 * e.g. { "app": { "name": "Voice Typer" } } → { "app.name": "Voice Typer" }
 */
export function flatten(
	obj: TranslationDict,
	prefix = "",
): Map<string, string> {
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

// Register English translations at module eval time. English is the
// universal fallback so it MUST be available synchronously — the
// dynamic-import path is only for non-English locales.
_translations.set("en", flatten(en as TranslationDict));
// DJ-95: defensive — drop any stale resolved-string cache for "en"
// (the cache is empty at module load, but this keeps the registration
// paths consistent with ensureLocaleLoaded/registerTranslations below).
_invalidateResolvedCache("en");

/**
 * Asynchronously load + register a non-English locale's translation
 * table via dynamic ``import()``. No-op for English (already loaded) or
 * for locales already loaded / in-flight.
 *
 * ER-65: previously all 8 locale JSON files were statically imported,
 * adding ~60 KB to the initial bundle and ~8 ms of parse time per
 * locale at boot — even though most users only ever see one locale.
 * The dynamic import is fire-and-forget: while the chunk loads,
 * ``t()`` falls back to English (the universal fallback already
 * encoded in the lookup path). Once the chunk resolves we register
 * the translations and notify subscribers (the ``useT`` hook) so
 * every subscribed component re-renders with the now-available
 * locale strings.
 *
 * @param locale The locale to load.
 * @returns A promise that resolves once the locale is registered (or
 *          immediately for English / already-loaded locales).
 */
export function ensureLocaleLoaded(locale: Locale): Promise<void> {
	// English is always loaded synchronously at module init.
	if (locale === "en") return Promise.resolve();
	// Already loaded — nothing to do.
	if (_translations.has(locale)) return Promise.resolve();
	// Already in-flight — return the pending promise so callers can
	// await it without spawning a duplicate request.
	const existing = _localeLoadPromises.get(locale);
	if (existing) return existing;

	_localeLoadInitiated.add(locale);
	const promise = (async () => {
		try {
			const mod = await import(
				/* @vite-ignore */ `./translations/${locale}.json`
			);
			const data = (mod as { default: TranslationDict }).default;
			_translations.set(locale, flatten(data));
			// DJ-95: drop the per-locale resolved-string cache so
			// the next ``t()`` call resolves against the freshly-
			// loaded map.
			_invalidateResolvedCache(locale);
			// Notify subscribers (the ``useT`` hook) so every
			// subscribed component re-renders with the now-available
			// locale strings. We use the same path as ``setLocale()``.
			notifyLocaleSubscribers();
		} catch (e) {
			// Dynamic import failed (corrupt chunk, network error,
			// unsupported locale at runtime). Leave English as the
			// active fallback — ``t()`` already falls back to English
			// when the current locale's map is missing.
			console.warn(`[i18n] dynamic import for "${locale}" failed:`, e);
		} finally {
			_localeLoadPromises.delete(locale);
		}
	})();
	_localeLoadPromises.set(locale, promise);
	return promise;
}

/**
 * Register translations for a locale.
 */
export function registerTranslations(
	locale: Locale,
	data: TranslationDict,
): void {
	_translations.set(locale, flatten(data));
	// DJ-95: invalidate the per-locale resolved-string cache so the
	// newly-registered translations are picked up by the next ``t()`` call.
	_invalidateResolvedCache(locale);
}

// ── Locale switch orchestrator ───────────────────────────────────

/**
 * Set the current locale and update the document text direction.
 *
 * F-4: When switching to an RTL locale (Arabic), sets
 * ``document.documentElement.dir = "rtl"`` so the entire UI flips
 * horizontally. Falls back to "ltr" for all other locales.
 *
 * Side effects beyond the renderer:
 *   - NH-2: kicks off the async dynamic-import of the newly-selected
 *     locale's translation table via ``ensureLocaleLoaded(next)`` so
 *     ``t()`` stops falling back to English after a runtime locale
 *     switch (previously the import was only triggered at module init
 *     for the restored/detected locale).
 *   - NH-3: pushes the locale to the Electron main process via
 *     ``window.window_.setLocale?.(locale)`` so native dialogs render
 *     in the user's selected language.
 *   - NH-4: pushes the locale + renderer-known tray-menu labels to the
 *     Python backend via ``window.python.call({ type:
 *     "set_tray_locale", data: { locale, labels } })`` so tray-menu
 *     items localise.
 *
 * Both IPC pushes are best-effort (the bridge surfaces may be missing
 * during module-init or under Tauri), so ``setLocale`` must NOT crash
 * when ``window.window_`` / ``window.python`` is undefined or when the
 * IPC promise rejects.
 */
export function setLocale(locale: Locale): void {
	let next: Locale = locale;
	if (!SUPPORTED_LOCALES.includes(locale)) {
		console.warn(`[i18n] Unsupported locale: ${locale}. Falling back to 'en'.`);
		next = "en";
	}
	_setCurrentLocale(next);

	// NH-2: kick off the dynamic import for non-English locales so the new
	// locale's strings are available without a page reload. Without this,
	// switching to e.g. Arabic at runtime would update `dir`/`lang` (visible
	// layout change) but `t()` would still return English until the user
	// reloads the page. `ensureLocaleLoaded` is idempotent — if the chunk
	// is already loaded or in-flight, this is a no-op. The promise it
	// returns resolves later and triggers a subscriber notification
	// (inside ensureLocaleLoaded), so subscribed components re-render with
	// the now-available strings.
	if (next !== "en") void ensureLocaleLoaded(next);

	// F-4: Update document direction for RTL support.
	try {
		if (typeof document !== "undefined") {
			document.documentElement.dir = isRtlLocale(next) ? "rtl" : "ltr";
			// CR-44: also set ``lang`` so screen readers pronounce content
			// in the user-selected UI locale (not the browser default).
			document.documentElement.lang = next;
		}
	} catch (e) {
		// SSR environments may not have document
		console.warn("[i18n] setLocale document dir/lang failed:", e);
	}

	// F-3: persist the choice so it survives restarts. Previously the
	// caller did this and relied on a full reload to re-read it.
	try {
		if (typeof localStorage !== "undefined") {
			localStorage.setItem("voice-typer-ui-locale", next);
		}
	} catch (e) {
		// localStorage may be unavailable in some contexts
		console.warn("[i18n] setLocale localStorage.setItem failed:", e);
	}

	// F-3: notify subscribers (the useT hook) so every subscribed
	// component re-renders with the new locale instead of requiring a
	// page reload.
	notifyLocaleSubscribers();

	// NH-3 / NH-4: best-effort push to the main process + Python backend.
	// The bridge surfaces may be missing (module-init scenario, Tauri host
	// without these IPC channels) — the push helpers swallow rejections
	// and sync throws so a locale-switch failure never breaks the UI.
	pushLocaleToMainProcess(next);
	pushLocaleToPythonBackend(next);
}
