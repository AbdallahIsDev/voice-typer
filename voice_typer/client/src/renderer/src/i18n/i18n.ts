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
//
// PVT-082: tChoice() provides ICU-style pluralization on top of t().
// PVT-083: when no locale is saved in localStorage, the user's preferred
// browser/OS language (navigator.languages) is matched against
// SUPPORTED_LOCALES so first-run users see their language automatically.

import { useSyncExternalStore } from "react";
// ER-65: ar/de/es/fr/hi/ru/zh dynamically imported via ensureLocaleLoaded()
import en from "./translations/en.json";

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

/**
 * NH-4: build a dictionary of tray-menu label keys → localized strings
 * for the current locale. Keys whose translation resolves to the raw key
 * itself (meaning the key is missing from both the current locale and
 * English) are excluded so the backend keeps its English defaults.
 *
 * The returned object is sent to the Python sidecar via
 * ``window.python.call({type: "set_tray_locale", data: {locale, labels}})``
 * so tray-menu items localise without a backend restart.
 */
export function trayLabelsForLocale(): Record<string, string> {
	const labels: Record<string, string> = {};
	const entries: [string, string][] = [
		["models", "models.title"],
		["microphones", "microphone.microphone"],
	];
	for (const [key, labelKey] of entries) {
		const value = t(labelKey);
		// Skip entries where the translation equals the raw key —
		// the key is missing from both the current locale and
		// English, so the backend should keep its default.
		if (value !== labelKey) {
			labels[key] = value;
		}
	}
	return labels;
}

// PVT-083: detect the user's preferred browser/OS language and match it
// against SUPPORTED_LOCALES. Returns the matched locale or "en" as the
// fallback. Used only on first run (when no locale is saved in
// localStorage).
//
// We consider both the full tag (e.g. "pt-BR") and the primary subtag
// (e.g. "pt") so a user with browser language "zh-CN" still matches our
// "zh" locale. We also normalise casing and ignore tags we don't ship.
function detectBrowserLocale(): Locale {
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

// Current locale — defaults to 'en'. Can be changed via setLocale().
// UX-015: restore from localStorage on module load so the user's
// language choice survives app restarts.
//
// PVT-083: when no locale has been explicitly chosen (no localStorage
// entry), fall back to the browser/OS language instead of forcing
// English. This means a user whose OS is set to French sees the French
// UI on first launch — they don't have to hunt for the language
// selector in Settings.
let _currentLocale: Locale = "en";
try {
	if (typeof localStorage !== "undefined") {
		const saved = localStorage.getItem("voice-typer-ui-locale");
		if (saved && (SUPPORTED_LOCALES as readonly string[]).includes(saved)) {
			_currentLocale = saved as Locale;
		} else {
			// PVT-083: no saved preference — auto-detect from the browser.
			_currentLocale = detectBrowserLocale();
		}
	}
} catch (e) {
	// localStorage may be unavailable in some contexts (SSR, sandboxed)
	console.warn("[i18n] module-load locale restore failed:", e);
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
} catch (e) {
	// document may be unavailable in some contexts (SSR, tests)
	console.warn("[i18n] initial document dir/lang set failed:", e);
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

// ER-65: locales whose dynamic import has already been kicked off.
// Prevents duplicate network requests when both the module-load
// auto-load AND the first ``t()`` call race for the same locale.
const _localeLoadInitiated: Set<Locale> = new Set();

// Pending dynamic-import promises — used to deduplicate concurrent
// ``ensureLocaleLoaded`` calls for the same locale.
const _localeLoadPromises: Map<Locale, Promise<void>> = new Map();

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
 * Note: ``_localeSubscribers`` is declared further down in the module
 * (with the other subscription plumbing). It's a ``const`` so the TDZ
 * would normally prevent this function from referencing it — but
 * ``ensureLocaleLoaded`` is only ever CALLED from ``setLocale`` /
 * module-init / ``t()``, all of which run after the module body has
 * finished evaluating, so by the time the dynamic-import promise
 * resolves and the subscriber loop runs, ``_localeSubscribers`` is
 * initialised.
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
			// Notify subscribers (the ``useT`` hook) so every
			// subscribed component re-renders with the now-available
			// locale strings. We use the same path as ``setLocale()``.
			for (const cb of _localeSubscribers) {
				try {
					cb();
				} catch (e) {
					console.warn("[i18n] locale-ready subscriber callback failed:", e);
				}
			}
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
	_currentLocale = next;

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
			document.documentElement.dir = RTL_LOCALES.has(next) ? "rtl" : "ltr";
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
	for (const cb of _localeSubscribers) {
		try {
			cb();
		} catch (e) {
			// a misbehaving subscriber must not break locale switching
			console.warn("[i18n] locale subscriber callback failed:", e);
		}
	}

	// NH-3 / NH-4: best-effort push to the main process + Python backend.
	// The bridge surfaces may be missing (module-init scenario, Tauri host
	// without these IPC channels) — the push helpers swallow rejections
	// and sync throws so a locale-switch failure never breaks the UI.
	pushLocaleToMainProcess(next);
	pushLocaleToPythonBackend(next);
}

/**
 * Best-effort push of the current locale to the Electron main process
 * via the ``window.window_.setLocale(locale)`` IPC bridge (registered
 * in ``main/ipc/window-handlers.ts`` as the ``i18n:set-locale``
 * handler). The main process uses the pushed locale to localise native
 * dialogs (single-instance error, critical-error dialog, model-folder
 * picker, export save-as dialogs).
 *
 * No-op when the bridge is missing (Tauri host, module-init scenario
 * where the preload bridge isn't installed yet). Rejections and sync
 * throws are caught and logged via ``console.warn`` so a locale switch
 * never crashes the renderer.
 */
function pushLocaleToMainProcess(locale: Locale): void {
	try {
		const bridge = (
			globalThis as unknown as {
				window_?: { setLocale?: (locale: string) => Promise<unknown> };
			}
		).window_;
		const result = bridge?.setLocale?.(locale);
		if (result && typeof (result as Promise<unknown>).then === "function") {
			(result as Promise<unknown>).catch((e: unknown) => {
				console.warn("[i18n] setLocale main-process push failed:", e);
			});
		}
	} catch (e: unknown) {
		console.warn("[i18n] setLocale main-process push failed:", e);
	}
}

/**
 * Best-effort push of the current locale + renderer-known tray-menu
 * labels to the Python backend via the ``set_tray_locale`` IPC message.
 * The backend uses the pushed locale + labels to localise the tray
 * menu (see ``voice_typer/server/tray_i18n.py``).
 *
 * No-op when the bridge is missing (Tauri host, module-init scenario).
 * Rejections and sync throws are caught and logged via ``console.warn``.
 *
 * The label map is built by the module-level {@link trayLabelsForLocale}
 * helper (declared above).
 */
function pushLocaleToPythonBackend(locale: Locale): void {
	try {
		const bridge = (
			globalThis as unknown as {
				python?: {
					call?: (msg: {
						type: string;
						data?: Record<string, unknown>;
					}) => Promise<unknown>;
				};
			}
		).python;
		const result = bridge?.call?.({
			type: "set_tray_locale",
			data: { locale, labels: trayLabelsForLocale() },
		});
		if (result && typeof (result as Promise<unknown>).then === "function") {
			(result as Promise<unknown>).catch((e: unknown) => {
				console.warn("[i18n] setLocale Python-backend push failed:", e);
			});
		}
	} catch (e: unknown) {
		console.warn("[i18n] setLocale Python-backend push failed:", e);
	}
}

/**
 * React hook that returns the {@link tChoice} pluralization function
 * bound to the current locale.
 *
 * XA-20-1 (Critical): ``tChoice()`` was implemented (PVT-082) but never
 * wired into any component — every pluralized string in the renderer
 * used the broken binary ``Singular`` / ``Plural`` key pattern, which
 * (a) only works for English-like 2-form locales, (b) leaks English
 * fallbacks for Slavic/Semitic locales that have 3-6 plural forms, and
 * (c) cannot adapt to ``Intl.PluralRules`` categories (``zero`` /
 * ``one`` / ``two`` / ``few`` / ``many`` / ``other``).
 *
 * Exposing a ``useTChoice()`` hook (mirroring the existing ``useT()``
 * pattern) lowers the barrier for components to adopt proper CLDR
 * pluralization. The hook subscribes to locale changes via
 * ``useSyncExternalStore`` so the returned ``tChoice`` reference always
 * resolves against the live current locale, and any component using it
 * re-renders when the locale changes.
 *
 * Usage:
 *   const tChoice = useTChoice();
 *   tChoice("inbox.messages", 1)    // "You have 1 unread message."
 *   tChoice("inbox.messages", 5)    // "You have 5 unread messages."
 *   tChoice("inbox.messages", 2, { name: "Alice" })
 *                                    // "You have 2 unread messages."
 *
 * Catalog keys (one entry per CLDR plural category the locale needs):
 *   {
 *     "inbox.messages_one":   "You have {count} unread message.",
 *     "inbox.messages_other": "You have {count} unread messages.",
 *     // Slavic example (Polish) — uses "few" + "many":
 *     "inbox.messages_few":   "Masz {count} nieprzeczytane wiadomości.",
 *     "inbox.messages_many":  "Masz {count} nieprzeczytanych wiadomości."
 *   }
 *
 * The returned function is the same {@link tChoice} export — wrapping
 * it in a hook purely exists to opt the calling component into
 * locale-change re-renders (calling ``tChoice`` directly outside a
 * hook would NOT trigger a re-render on locale change).
 */
export function useTChoice(): typeof tChoice {
	useSyncExternalStore(subscribeLocale, getLocaleSnapshot, getLocaleSnapshot);
	return tChoice;
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
			result = result.replace(interpRegex(k), v);
		}
	}
	return result;
}

// ── PVT-082: pluralization support ───────────────────────────────
//
// tChoice() resolves a pluralized translation key using the CLDR plural
// rules for the current locale. The lookup order is:
//
//   1. Look up the locale-specific CLDR plural category for `count` via
//      `Intl.PluralRules` (categories: "zero", "one", "two", "few",
//      "many", "other").
//   2. Try the catalog key `{key}_{category}` (e.g. `inbox.messages_one`,
//      `inbox.messages_other`, `inbox.messages_few` for Slavic locales).
//   3. Fall back to `{key}_other` (the universal CLDR fallback) if the
//      category-specific key is missing.
//   4. Fall back to the bare `key` (no suffix) for catalogs that haven't
//      been pluralized yet — preserves backwards compatibility with
//      existing single-form strings.
//   5. Last resort: return the raw key (matching `t()` semantics).
//
// After resolving the catalog value, `{placeholder}` interpolation runs
// just like `t()` — pass `{ count: "5" }` (or any other params) to
// substitute into the resolved string. The `count` used for plural
// selection is automatically exposed as `{count}` in the params for
// convenience, mirroring ICU MessageFormat semantics.
//
// Example catalog:
//   {
//     "inbox": {
//       "messages_one": "You have {count} unread message.",
//       "messages_other": "You have {count} unread messages.",
//       "messages_few": "Masz {count} nieprzeczytane wiadomości."  // Polish
//     }
//   }
//
// Example call:
//   tChoice("inbox.messages", 1)         → "You have 1 unread message."
//   tChoice("inbox.messages", 5)         → "You have 5 unread messages."
//   tChoice("inbox.messages", 2, {name: "Alice"})
//                                         → "You have 2 unread messages."
//                                         (and {name} would be interpolated too)

// ER-20: cache the per-parameter interpolation RegExp. ``t()`` /
// ``tChoice()`` previously built a fresh ``new RegExp(`\\{${k}\\}`, "g")``
// for every parameter of every call — under a hot render path with
// several interpolations per string this allocated thousands of
// short-lived RegExp objects per second. The keyspace is tiny (only a
// handful of distinct placeholder names — ``count``, ``name``, …) so a
// Map<string, RegExp> cache reuses the same RegExp instance forever.
const _interpCache = new Map<string, RegExp>();

/**
 * Get (or create) the cached interpolation RegExp for a placeholder key.
 * The RegExp matches the literal ``{key}`` token globally so it can be
 * passed to ``String.prototype.replace`` for substitution.
 */
function interpRegex(key: string): RegExp {
	let r = _interpCache.get(key);
	if (!r) {
		r = new RegExp(`\\{${key}\\}`, "g");
		_interpCache.set(key, r);
	}
	return r;
}

// Cache Intl.PluralRules instances per locale — constructing one is
// expensive enough that we don't want to do it on every tChoice() call.
const _pluralRulesCache: Map<Locale, Intl.PluralRules> = new Map();

/**
 * Get (or create) an Intl.PluralRules instance for the given locale.
 * Returns the cached instance if available.
 */
function getPluralRules(locale: Locale): Intl.PluralRules {
	let rules = _pluralRulesCache.get(locale);
	if (!rules) {
		try {
			rules = new Intl.PluralRules(locale);
		} catch {
			// Some environments may not support Intl.PluralRules for
			// every locale tag. Fall back to English rules, which
			// only distinguish "one" (count === 1) from "other".
			try {
				rules = new Intl.PluralRules("en");
			} catch {
				// Last resort: a stub that always returns "other".
				// This means pluralized keys degrade to the
				// "_other" form, which is always valid CLDR.
				rules = {
					select: () => "other",
					resolvedOptions: () => ({
						locale: "en",
						type: "cardinal" as const,
						minimumIntegerDigits: 1,
						minimumFractionDigits: 0,
						maximumFractionDigits: 3,
						minimumSignificantDigits: 1,
						maximumSignificantDigits: 21,
						roundingIncrement: 1,
						roundingMode: "halfExpand" as const,
						roundingPriority: "auto" as const,
						trailingZeroDisplay: "auto" as const,
					}),
				} as unknown as Intl.PluralRules;
			}
		}
		_pluralRulesCache.set(locale, rules);
	}
	return rules;
}

/**
 * Resolve a pluralized translation key for the given count.
 *
 * See the PVT-082 section above for the full lookup algorithm.
 *
 * @param key - Dot-separated base key (e.g., "inbox.messages")
 * @param count - The numeric count that determines the plural category
 * @param params - Optional additional interpolation params. The count is
 *                 automatically exposed as `{count}` (stringified) unless
 *                 the caller overrides it.
 * @returns The translated, interpolated string
 */
export function tChoice(
	key: string,
	count: number,
	params?: Record<string, string>,
): string {
	const category = getPluralRules(_currentLocale).select(count);
	// Build the candidate keys in fallback order:
	//   1. {key}_{category}    (e.g. "inbox.messages_one")
	//   2. {key}_other         (CLDR universal fallback)
	//   3. {key}               (bare key — backwards compat)
	const candidates = [`${key}_${category}`, `${key}_other`, key];

	let resolved: string | undefined;
	const currentMap = _translations.get(_currentLocale);
	const enMap = _translations.get("en");
	for (const candidate of candidates) {
		if (currentMap?.has(candidate)) {
			resolved = currentMap.get(candidate);
			break;
		}
		if (enMap?.has(candidate)) {
			resolved = enMap.get(candidate);
			break;
		}
	}
	if (resolved === undefined) {
		// Nothing found — return the bare key (matches t() semantics).
		return key;
	}
	// Auto-expose `count` as a stringified interpolation param unless
	// the caller explicitly overrides it.
	const merged: Record<string, string> = { count: String(count) };
	if (params) {
		for (const [k, v] of Object.entries(params)) {
			merged[k] = v;
		}
	}
	for (const [k, v] of Object.entries(merged)) {
		resolved = resolved.replace(interpRegex(k), v);
	}
	return resolved;
}

// ER-65: kick off the async load of the user's restored/detected
// locale at module init so the dynamic import is in-flight by the time
// the first ``t()`` call happens. English (the universal fallback) is
// already registered synchronously above, so ``t()`` returns English
// strings until the dynamic chunk resolves — then ``useT`` subscribers
// are notified and the UI repaints with the user's selected locale.
// The fire-and-forget pattern means module load stays synchronous.
if (_currentLocale !== "en") {
	void ensureLocaleLoaded(_currentLocale);
}
