// ``t`` (translate) and ``tChoice`` (pluralize) functions + their caches.
//
//sub-finding (1-N Finding 6): the dead PluralRules stub at the
// old i18n.ts L648-664 has been deleted. The fallback path now uses
// ``new Intl.PluralRules("en")`` as the single fallback; if even that
// throws (no Intl runtime), a clear error is raised rather than
// silently degrading to a stub.
//
// Reads shared state (``_currentLocale`` via ``getLocale``, ``_translations``)
// from ``./store`` — never mutates it directly.

import type { Locale } from "./locale";
import { _translations, getLocale } from "./store";

//cache the per-parameter interpolation RegExp. ``t()`` /
// ``tChoice()`` previously built a fresh ``new RegExp(`\\{${k}\\}`, "g")``
// for every parameter of every call — under a hot render path with
// several interpolations per string this allocated thousands of
// short-lived RegExp objects per second. The keyspace is tiny (only a
// handful of distinct placeholder names — ``count``, ``name``, …) so a
// Map<string, RegExp> cache reuses the same RegExp instance forever.
export const _interpCache = new Map<string, RegExp>();

//per-(locale, key) resolved-string cache.
//
// ``t()`` previously walked the locale → English → key fallback chain
// on every call. With ~77 ``t()`` calls in ``AudioFilterChain.tsx``
// alone and dozens more across Dashboard/Home/Settings, the Map.has +
// Map.get chain runs hundreds of times per render. The keyspace
// (locale, key) is small and stable, so memoizing the resolved string
// (before interpolation) skips the lookup chain on hits.
//
// Invalidation: when a locale's translation Map is replaced (via
// ``registerTranslations`` or ``ensureLocaleLoaded`` in store.ts), the
// entire per-locale cache entry is dropped via
// ``_invalidateResolvedCache``.
export const _resolvedCache: Map<Locale, Map<string, string>> = new Map();

/**
 * Drop the cached resolved strings for a locale. Called when a locale's
 * translation table is registered/replaced (e.g. via the dynamic
 * import path in ``ensureLocaleLoaded``) so stale strings don't linger.
 */
export function _invalidateResolvedCache(locale: Locale): void {
	_resolvedCache.delete(locale);
}

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
export const _pluralRulesCache: Map<Locale, Intl.PluralRules> = new Map();

/**
 * Get (or create) an Intl.PluralRules instance for the given locale.
 * Returns the cached instance if available.
 *
 *  sub-finding (1-N Finding 6): the dead PluralRules stub fallback
 * has been removed. If the requested locale fails AND the English
 * fallback fails (no Intl runtime), we rethrow with a clear message
 * instead of silently degrading — silent degradation hid a real
 * runtime-availability bug.
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
			} catch (enErr) {
				// Last resort used to be a stub that always returned
				// "other" — but if even English PluralRules fails,
				// the Intl runtime is fundamentally broken. Surface
				// the error loudly so it's not silently masked.
				throw new Error(
					`[renderer:i18n] Intl.PluralRules is unavailable for locale "${locale}" and the English fallback also failed: ` +
						`${(enErr as Error)?.message ?? enErr}`,
				);
			}
		}
		_pluralRulesCache.set(locale, rules);
	}
	return rules;
}

/**
 * Translate a key to the current locale's string.
 *
 * Lookup chain (in order):
 *
 *   1. ``currentLocale`` — the active UI locale's translation map.
 *   2. ``primary subtag`` — when the current locale is a regional
 *      variant (contains ``-``), try the bare primary subtag's map
 *      before falling back to English. e.g. ``zh-CN`` → ``zh`` → ``en``.
 *      Bare primaries (``en``, ``zh``, ``ar`` …) skip this step because
 *      the subtag would equal the locale itself.
 *   3. ``en`` — the universal fallback. English is always loaded
 *      synchronously at module init (see ``store.ts``) so this step
 *      never blocks on a dynamic import.
 *   4. the raw key — defensive last resort so callers don't crash on
 *      a typo. In dev mode (``import.meta.env?.DEV``) this step also
 *      emits a ``console.warn`` so a misspelled or absent key surfaces
 *      during QA instead of silently rendering the literal key string
 *      in production UI.
 *
 * Supports optional ``{placeholder}`` interpolation: if ``params`` is
 * provided, each ``{key}`` in the translated string is replaced with
 * the corresponding value from ``params``.
 *
 * @param key - Dot-separated translation key (e.g., "app.name")
 * @param params - Optional interpolation params (e.g., `{ key: "Esc" }`)
 * @returns The translated string
 */
export function t(key: string, params?: Record<string, string>): string {
	let result: string;
	const currentLocale = getLocale();

	//per-(locale, key) resolved-string cache. The cached value
	// is the pre-interpolation template, so we still run interpolation
	// after the cache hit — only the lookup chain is short-circuited.
	//
	// The cache also memoizes the raw-key fallback (step 4 below) so a
	// missing key warns at most once per (locale, key) pair — subsequent
	// calls return the cached raw key without re-warning. This keeps
	// dev-mode console output readable without losing the first-occurrence
	// signal that surfaces a typo.
	let cachedLocale = _resolvedCache.get(currentLocale);
	if (cachedLocale !== undefined) {
		const cached = cachedLocale.get(key);
		if (cached !== undefined) {
			result = cached;
			if (params) {
				for (const [k, v] of Object.entries(params)) {
					result = result.replace(interpRegex(k), v);
				}
			}
			return result;
		}
	}

	// Resolve against the lookup chain:
	//   currentLocale → primary subtag (if regional) → en → raw key
	let missedKey = false;
	const currentMap = _translations.get(currentLocale);
	if (currentMap?.has(key)) {
		result = currentMap.get(key) ?? key;
	} else if (currentLocale.includes("-")) {
		// Regional variant (e.g. ``zh-CN``, ``pt-BR``). Try the primary
		// subtag's map before falling back to English — a translator
		// adding a regional override for a handful of keys should not
		// silently lose the parent language's coverage for the rest.
		const primary = currentLocale.split("-")[0] as Locale;
		const primaryMap = _translations.get(primary);
		if (primaryMap?.has(key)) {
			result = primaryMap.get(key) ?? key;
		} else {
			const enMap = _translations.get("en");
			if (enMap?.has(key)) {
				result = enMap.get(key) ?? key;
			} else {
				result = key;
				missedKey = true;
			}
		}
	} else {
		// Bare primary locale (no ``-``). Skip the redundant primary-
		// subtag step and go straight to English.
		const enMap = _translations.get("en");
		if (enMap?.has(key)) {
			result = enMap.get(key) ?? key;
		} else {
			result = key;
			missedKey = true;
		}
	}

	// Dev-mode missing-key diagnostic. Production builds skip the check
	// (``import.meta.env?.DEV`` is ``false`` in production per Vite, and
	// the optional chain short-circuits to ``undefined`` in non-Vite
	// environments like SSR). Vitest runs with ``DEV=true`` so the
	// warning fires during tests — see ``translate-fallback.test.ts``.
	if (missedKey && import.meta.env?.DEV) {
		console.warn(
			"[renderer:i18n] missing key:",
			key,
			"for locale:",
			currentLocale,
		);
	}

	//store the resolved (pre-interpolation) template so the
	// next call with the same (locale, key) skips the lookup chain.
	if (cachedLocale === undefined) {
		cachedLocale = new Map<string, string>();
		_resolvedCache.set(currentLocale, cachedLocale);
	}
	cachedLocale.set(key, result);
	// Interpolate {placeholder} values
	if (params) {
		for (const [k, v] of Object.entries(params)) {
			result = result.replace(interpRegex(k), v);
		}
	}
	return result;
}

//pluralization support ───────────────────────────────
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

/**
 * Resolve a pluralized translation key for the given count.
 *
 * See the  section above for the full lookup algorithm.
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
	const currentLocale = getLocale();
	const category = getPluralRules(currentLocale).select(count);
	// Build the candidate keys in fallback order:
	//   1. {key}_{category}    (e.g. "inbox.messages_one")
	//   2. {key}_other         (CLDR universal fallback)
	//   3. {key}               (bare key — backwards compat)
	const candidates = [`${key}_${category}`, `${key}_other`, key];

	let resolved: string | undefined;
	const currentMap = _translations.get(currentLocale);
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
