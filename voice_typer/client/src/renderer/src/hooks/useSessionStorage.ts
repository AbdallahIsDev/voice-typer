import { useCallback, useState } from "react";

export function useSessionStorage<T>(
	key: string,
	initialValue: T,
): [T, (value: T | ((prev: T) => T)) => void] {
	const [stored, setStored] = useState<T>(() => {
		try {
			const item = sessionStorage.getItem(key);
			return item ? (JSON.parse(item) as T) : initialValue;
		} catch {
			return initialValue;
		}
	});

	const setValue = useCallback(
		(value: T | ((prev: T) => T)) => {
			setStored((prev) => {
				const next = value instanceof Function ? value(prev) : value;
				try {
					sessionStorage.setItem(key, JSON.stringify(next));
				} catch {
					/* ignore, storage may be unavailable (private mode,
                                           sandbox restrictions). The in-memory state still
                                           updates; only the persistence is best-effort. */
				}
				return next;
			});
		},
		[key],
	);

	return [stored, setValue];
}
