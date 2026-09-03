// Stale-while-revalidate cache for IPC read responses (module-level,
// session lifetime — vercel-react-best-practices: client-swr-dedup /
// js-cache-function-results pattern).
//
// Problem: every data page (History / Templates / Vocabulary /
// Analytics / Models / Microphone / Settings) fetches its data from the
// Python sidecar in a mount effect and rendered a centered spinner
// until the round-trip resolved. Navigating away and back re-ran the
// whole fetch → spinner → content cycle every time, even though the
// data rarely changed between visits.
//
// Fix: hooks seed their React state from `peekIpcCache` on mount (so a
// revisit renders CACHED content on the first paint) and write through
// `writeIpcCache` after each successful fetch (so the next visit sees
// fresh data). The mount fetch still runs — it just revalidates in the
// background instead of gating the UI behind a loading state.
//
// Scope: cache keys are per-feature strings owned by the hooks that
// read/write them (e.g. "vocabulary.entries"). Only READ-shaped
// responses are cached; mutations (save/delete/clear) go through the
// same `call` pipe untouched and simply rewrite the cache on success.

const store = new Map<string, unknown>();

/** Read the cached value for `key`, or undefined on a miss. */
export function peekIpcCache<T>(key: string): T | undefined {
	return store.get(key) as T | undefined;
}

/** Write-through helper — hooks call this after a successful fetch. */
export function writeIpcCache(key: string, value: unknown): void {
	store.set(key, value);
}

/**
 * Test seam (same pattern as `_resetNavigationForTest`): vitest's
 * global `afterEach` (test-setup.ts) clears the cache between tests so
 * every test starts from the cold-start state a real user gets on app
 * launch — without this, a test's successful fetch would leak into the
 * next test's "first load" assertions.
 */
export function __resetIpcCacheForTests(): void {
	store.clear();
}
