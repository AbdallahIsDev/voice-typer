import { useCallback, useState } from "react";

/**
 * ``useSessionStorage``, a ``useState``-shaped hook that persists its
 * value to ``sessionStorage`` so the value survives page navigation
 * within the same browser session (per-tab / per-Electron-window).
 *
 * Mirrors the ``useState`` API: pass an initial value, get back a tuple
 * of ``[value, setValue]`` where ``setValue`` accepts either a plain
 * value or an updater function. JSON-serialised on every set; the
 * stored JSON is parsed back on the first mount.
 *
 * No cross-tab propagation, deliberately. The window ``storage``
 * event does NOT fire in other tabs/windows for ``sessionStorage``
 * writes (per MDN, the event syncs other documents only for
 * ``localStorage``; ``sessionStorage`` is per-tab by design, and the
 * document that makes a change never receives its own event). A
 * previous version registered a ``storage`` listener here that could
 * never fire cross-tab, dead code removed; filter state stays per-tab.
 *
 * Failure modes are silent + non-fatal: a corrupt JSON blob, a
 * disabled ``sessionStorage`` (private mode, sandbox restrictions), or
 * a thrown serialisation error all fall back to the initial value
 * without crashing the renderer. This matches the contract callers
 * rely on (filter state is best-effort persistence, losing it never
 * breaks the page).
 *
 * Naming: the ``vt:`` prefix used by callers (via ``useFilterState``)
 * namespaces the renderer's sessionStorage keys away from anything
 * the backend or other libs might set in the same origin.
 */
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
