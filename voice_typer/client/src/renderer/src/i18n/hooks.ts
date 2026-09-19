// React hooks + locale-change subscriber registry.
//The subscriber set + ``getLocaleSnapshot`` live here (per the
// split plan) so that layout/component code that ONLY needs the React
// subscription surface can import this one small module instead of the
// full i18n package (which pulls in the translation tables, plural
// caches, IPC push helpers, etc.).
// ``setLocale`` (in ``./store``) and ``ensureLocaleLoaded`` (in
// ``./store``) call ``notifyLocaleSubscribers`` from this module to
// re-render every ``useT`` / ``useTChoice`` subscriber when the active
// locale's translation table changes.
// Note: this module imports ``getLocale`` from ``./store``, and
// ``./store`` imports ``notifyLocaleSubscribers`` from here. The
// resulting ESM cycle is safe, both modules only invoke each other's
// exports from inside function bodies (no top-level value access), so
// by the time either function runs, both modules have finished
// evaluating.

import { useSyncExternalStore } from "react";
import { getLocale } from "./store";
import { t, tChoice } from "./translate";

// `t()` is a plain function with no React subscription, so switching
// repaint every component. We now keep a subscriber set and notify it
// from `setLocale` / `ensureLocaleLoaded`, letting the `useT()` hook
// (useSyncExternalStore) re-render subscribed components in place.
export const _localeSubscribers: Set<() => void> = new Set();

export function subscribeLocale(cb: () => void): () => void {
	if (typeof cb !== "function") return () => {};
	_localeSubscribers.add(cb);
	return () => {
		_localeSubscribers.delete(cb);
	};
}

// Monotonic revision counter bumped on EVERY subscriber notification.
// `useSyncExternalStore` only re-renders when the snapshot VALUE changes.
// `setLocale` notifies BEFORE the dynamic-imported translation table for
// the new locale is registered (t() falls back to English), and
// `ensureLocaleLoaded` notifies again AFTER the table is ready. If the
// snapshot were just the locale string, the second notification would
// see an unchanged snapshot ("ar" == "ar") and `useSyncExternalStore`
// would skip the re-render, leaving every `useT()` component stuck on
// regression: switching to Arabic without a page reload never showed
// Arabic labels). Including the revision makes both notifications
// observable.
let _localeRevision = 0;

/** Snapshot of the current locale + revision, used as `getSnapshot`
 *  for `useSyncExternalStore`. */
export function getLocaleSnapshot(): string {
	return `${getLocale()}#${_localeRevision}`;
}

export function notifyLocaleSubscribers(): void {
	_localeRevision++;
	for (const cb of _localeSubscribers) {
		try {
			cb();
		} catch (e) {
			// a misbehaving subscriber must not break locale switching
			console.warn("[renderer:i18n] locale subscriber callback failed:", e);
		}
	}
}

export function useT(): typeof t {
	useSyncExternalStore(subscribeLocale, getLocaleSnapshot, getLocaleSnapshot);
	return t;
}

export function useTChoice(): typeof tChoice {
	useSyncExternalStore(subscribeLocale, getLocaleSnapshot, getLocaleSnapshot);
	return tChoice;
}
