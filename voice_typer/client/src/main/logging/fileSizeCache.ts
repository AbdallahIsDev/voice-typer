/**
 * XV-154 file-size cache for the Electron main-process loggers.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). `_fileSizeCache` memoizes `fs.statSync` results so
 * `appendLogLine` doesn't call `fs.statSync` on every write — only on
 * cache miss. The cache is bumped (updated) after every successful
 * append so the next call can skip stat. Rotations reset the cache
 * entry to 0.
 *
 * Leaf module — no imports. Consumed by `rotation.ts` (via
 * `_getCachedFileSize` / `_setCachedFileSize` / `_clearCachedFileSize`)
 * and by the test suite (via `_resetFileSizeCacheForTest`).
 */

// ────────────────────────────────────────────────────────────────────
// XV-154: _fileSizeCache memoizes statSync results so appendLogLine
// doesn't call fs.statSync on every write — only on cache miss. The
// cache is bumped (updated) after every successful append so the
// next call can skip stat. Rotations reset the cache entry to 0.
// ────────────────────────────────────────────────────────────────────

/** @internal module-level cache keyed by absolute log-file path. */
const _fileSizeCache = new Map<string, number>();

/**
 * XV-154: reset the file-size cache. Exported for tests so each
 * test starts with a clean cache state.
 */
export function _resetFileSizeCacheForTest(): void {
	_fileSizeCache.clear();
}

/**
 * Read the cached file size for `filePath`. Returns `null` on cache
 * miss (caller should stat the real file).
 */
export function _getCachedFileSize(filePath: string): number | null {
	const cached = _fileSizeCache.get(filePath);
	return cached !== undefined ? cached : null;
}

/**
 * Update the cached file size for `filePath` after a successful append.
 */
export function _setCachedFileSize(filePath: string, size: number): void {
	_fileSizeCache.set(filePath, size);
}

/**
 * Remove an entry from the cache (used by `rotateIfNeeded` after rotation).
 */
export function _clearCachedFileSize(filePath: string): void {
	_fileSizeCache.delete(filePath);
}
