import { useEffect, useRef, useState } from "react";
import { useLatestRef } from "@/hooks/useLatestRef";

export interface DebouncedCallback<A extends unknown[]> {
	debounced: (...args: A) => void;
	cancel: () => void;
	flush: () => void;
}

// Framework-free debounce factory for module-level callers (theme
// persist) that cannot use hooks. Latest args win; flush fires pending
// work immediately; cancel drops it.
export function createDebouncedCallback<A extends unknown[]>(
	fn: (...args: A) => unknown,
	delay: number,
): DebouncedCallback<A> {
	let timer: ReturnType<typeof setTimeout> | null = null;
	let latestArgs: A | null = null;
	const fire = () => {
		timer = null;
		const args = latestArgs;
		latestArgs = null;
		if (args) fn(...args);
	};
	return {
		debounced: (...args: A) => {
			latestArgs = args;
			if (timer) clearTimeout(timer);
			timer = setTimeout(fire, delay);
		},
		cancel: () => {
			if (timer) {
				clearTimeout(timer);
				timer = null;
			}
			latestArgs = null;
		},
		flush: () => {
			if (timer) {
				clearTimeout(timer);
				fire();
			} else if (latestArgs) {
				fire();
			}
		},
	};
}

// React wrapper: latest fn always fires (no dep churn), pending work
// auto-cancels on unmount so timers never fire post-unmount.
export function useDebouncedCallback<A extends unknown[]>(
	fn: (...args: A) => unknown,
	delay: number,
): DebouncedCallback<A> {
	const fnRef = useLatestRef(fn);
	const handleRef = useRef<DebouncedCallback<A> | null>(null);
	if (!handleRef.current) {
		handleRef.current = createDebouncedCallback(
			(...args: A) => fnRef.current(...args),
			delay,
		);
	}
	useEffect(() => {
		const handle = handleRef.current;
		return () => {
			handle?.cancel();
		};
	}, []);
	return handleRef.current;
}

// Value form: mirrors useState but only publishes after `delay` of no
// changes; unmount/rapid-change cleanup via the effect return.
export function useDebouncedValue<T>(value: T, delay: number): T {
	const [debounced, setDebounced] = useState(value);
	useEffect(() => {
		const id = setTimeout(() => setDebounced(value), delay);
		return () => clearTimeout(id);
	}, [value, delay]);
	return debounced;
}
