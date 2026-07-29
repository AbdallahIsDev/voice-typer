// React hooks + locale-change subscriber registry.
//
// The subscriber set + ``getLocaleSnapshot`` live here (per the DR-31
// split plan) so that layout/component code that ONLY needs the React
// subscription surface can import this one small module instead of the
// full i18n package (which pulls in the translation tables, plural
// caches, IPC push helpers, etc.).
//
// ``setLocale`` (in ``./store``) and ``ensureLocaleLoaded`` (in
// ``./store``) call ``notifyLocaleSubscribers`` from this module to
// re-render every ``useT`` / ``useTChoice`` subscriber when the active
// locale's translation table changes.
//
// Note: this module imports ``getLocale`` from ``./store``, and
// ``./store`` imports ``notifyLocaleSubscribers`` from here. The
// resulting ESM cycle is safe — both modules only invoke each other's
// exports from inside function bodies (no top-level value access), so
// by the time either function runs, both modules have finished
// evaluating.

import { useSyncExternalStore } from "react";

import { getLocale } from "./store";
import { t, tChoice } from "./translate";
import type { Locale } from "./locale";

// ── Locale change subscription (b-review Finding 3) ──────────────
// `t()` is a plain function with no React subscription, so switching
// the locale used to require a full `window.location.reload()` to
// repaint every component. We now keep a subscriber set and notify it
// from `setLocale` / `ensureLocaleLoaded`, letting the `useT()` hook
// (useSyncExternalStore) re-render subscribed components in place.
export const _localeSubscribers: Set<() => void> = new Set();

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
	return getLocale();
}

/**
 * Notify every registered subscriber that the active locale (or its
 * translation table) has changed. Called by {@link setLocale} (in
 * ``./store``) after the locale mutates and by ``ensureLocaleLoaded``
 * (in ``./store``) after a dynamic-import chunk resolves and registers
 * a new translation table.
 *
 * Each subscriber callback is wrapped in its own try/catch so a
 * misbehaving subscriber cannot break the locale-switch path for the
 * rest of the UI.
 */
export function notifyLocaleSubscribers(): void {
	for (const cb of _localeSubscribers) {
		try {
			cb();
		} catch (e) {
			// a misbehaving subscriber must not break locale switching
			console.warn("[i18n] locale subscriber callback failed:", e);
		}
	}
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
