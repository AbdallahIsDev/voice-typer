// Timestamped snapshot helpers for the SWR-seed pattern (C-CACHE-1..5).
// ipcCache itself is an untyped timeless Map; these helpers add the two
// things every data hook needs on top: a `fetchedAt` freshness stamp
// (the TanStack staleTime equivalent) and a shared in-flight registry
// so hover prefetch and mount piggyback instead of duplicating.
// Shapes stay per-hook (each page owns its snapshot type + key); only
// the clock and the flight dedup are shared here.

/** Minimal call shape a data prefetch needs (the hooks' WindowCall
 *  satisfies this; the Sidebar's raw bridge call does too). */
export type PrefetchCall = (
	type: string,
	data?: Record<string, unknown>,
) => Promise<unknown>;

/** Every snapshot carries when it was written (ms epoch). */
export interface TimestampedSnapshot {
	fetchedAt: number;
}

/** True when the snapshot exists and is younger than `ttlMs`. */
export function isSnapshotFresh(
	snap: TimestampedSnapshot | null | undefined,
	ttlMs: number,
): boolean {
	return (
		snap != null && snap.fetchedAt > 0 && Date.now() - snap.fetchedAt < ttlMs
	);
}

const flights = new Map<string, Promise<void>>();

/** In-flight prefetch for `key`, if any (mount awaits it). */
export function peekPrefetchFlight(key: string): Promise<void> | null {
	return flights.get(key) ?? null;
}

// Mount re-race eligibility window: a mount load that failed FAST
// (refusal before the backend was up) earns one delayed re-race. A
// failure that already took longer had the bridge's cold-start patience
// applied upstream — re-racing it would stack a second ~90s wait onto
// the first, so it goes straight to the error screen instead.
export const MOUNT_RERACE_FAST_WINDOW_MS = 30_000;

// Best-effort prefetch runner: concurrent callers share one flight,
// failures never surface (the mount fetch stays authoritative), the
// entry clears on settle so the next stale hover re-fetches.
export function runPrefetchFlight(
	key: string,
	fn: () => Promise<void>,
): Promise<void> {
	const existing = flights.get(key);
	if (existing) return existing;
	const flight = (async () => {
		try {
			await fn();
		} catch {
			// Swallow: hover must never surface errors.
		} finally {
			flights.delete(key);
		}
	})();
	flights.set(key, flight);
	return flight;
}

/** Test-only reset for the flight registries. */
export function __resetPrefetchFlightsForTests(): void {
	flights.clear();
	valueFlights.clear();
}

const valueFlights = new Map<string, Promise<unknown>>();

// Value-sharing flight: concurrent callers share one underlying fetch
// AND its result (the disk-stat IPC that Analytics + Models fire
// together). Rejections propagate to all awaiters; the entry clears on
// settle so the next stale read re-fetches.
export function shareFlight<T>(key: string, fn: () => Promise<T>): Promise<T> {
	const existing = valueFlights.get(key);
	if (existing) return existing as Promise<T>;
	const flight = (async (): Promise<T> => {
		try {
			return await fn();
		} finally {
			valueFlights.delete(key);
		}
	})();
	valueFlights.set(key, flight);
	return flight;
}

/** In-flight value flight for `key`, if any. */
export function peekValueFlight<T>(key: string): Promise<T> | null {
	return (valueFlights.get(key) as Promise<T> | undefined) ?? null;
}
