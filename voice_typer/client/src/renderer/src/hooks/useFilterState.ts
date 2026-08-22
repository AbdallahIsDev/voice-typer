import { useCallback } from "react";

import { useSessionStorage } from "@/hooks/useSessionStorage";

/**
 * ``useFilterState`` — a typed wrapper around ``useSessionStorage`` for
 * page-level filter state (search query, sort order, tab selection,
 * expand/collapse toggles — anything the user sets on a list page and
 * expects to still be set when they navigate away and back).
 *
 * The key is namespaced per-page (``vt:filters:${page}``) so each page
 * owns its own slot in sessionStorage without colliding with the others.
 * Callers pass the page name (e.g. ``"vocabulary"``, ``"templates"``,
 * ``"microphone"``, ``"models"``) plus an optional sub-key (e.g.
 * ``"searchQuery"``, ``"sortOrder"``, ``"activeTab"``) — the composed
 * key ``vt:filters:${page}.${subKey}`` makes the slots greppable and
 * individually clearable.
 *
 * The returned tuple matches ``useState``'s shape so existing call sites
 * that used ``useState`` for these values can swap to ``useFilterState``
 * with no other code change (same ``[value, setter]`` API).
 *
 * DRY: every page that persists filter state uses this hook — there is
 * exactly ONE definition of the sessionStorage prefix and ONE definition
 * of the key format. Pages don't re-implement the prefix logic.
 */
export function useFilterState<T>(
	page: string,
	subKey: string,
	initialValue: T,
): [T, (value: T | ((prev: T) => T)) => void] {
	const storageKey = `vt:filters:${page}.${subKey}`;
	const [value, setValue] = useSessionStorage<T>(storageKey, initialValue);

	// Wrap the setter so the page-name argument is captured once at the
	// call site; callers don't need to know the key format. The setter
	// identity is stable across renders (depends only on the page name +
	// subKey, both of which are usually literal strings) — so it's safe
	// to pass as a dependency to other useCallback / useMemo.
	const set = useCallback(setValue, [setValue]);

	return [value, set];
}
