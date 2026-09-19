import { useCallback } from "react";

import { useSessionStorage } from "@/hooks/useSessionStorage";

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
	// subKey, both of which are usually literal strings), so it's safe
	// to pass as a dependency to other useCallback / useMemo.
	const set = useCallback(setValue, [setValue]);

	return [value, set];
}
