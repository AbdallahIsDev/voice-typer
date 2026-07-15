/**
 * Semantic version comparison utilities.
 *
 * b-review Finding 9 (F3): About.tsx previously compared version strings
 * using lexicographic comparison (`remote > APP_VERSION`), which breaks
 * for normal semver ordering — e.g. `"1.10.0" < "1.9.0"` lexicographically
 * even though `1.10.0 > 1.9.0` numerically.
 *
 * `compareSemver` splits each version on `.`, parses each part as an
 * integer, and compares pairwise (missing parts are treated as 0). It
 * returns -1 / 0 / 1 like Python's `cmp` builtin so callers can express
 * "is remote newer?" as `compareSemver(remote, APP_VERSION) > 0`.
 */

/**
 * Compare two semantic version strings.
 *
 * Each version is split on `.` and each part is parsed as a base-10
 * integer. Parts missing from one side are treated as `0`, so
 * `compareSemver("1.0", "1.0.0") === 0`. Non-numeric parts fall back
 * to 0 (so `"1.0.0-rc1"` is treated as `"1.0.0"` for comparison
 * purposes — prerelease tags are intentionally not honoured here
 * because the GitHub releases tag we compare against is always a
 * clean `vMAJOR.MINOR.PATCH`).
 *
 * @returns -1 if `a < b`, 0 if `a === b`, 1 if `a > b`.
 */
export function compareSemver(a: string, b: string): -1 | 0 | 1 {
	const partsA = a.split(".").map((p) => Number.parseInt(p, 10));
	const partsB = b.split(".").map((p) => Number.parseInt(p, 10));
	const len = Math.max(partsA.length, partsB.length);
	for (let i = 0; i < len; i++) {
		// Missing parts (out-of-bounds index returns undefined, which
		// Number.isNaN() reports as false because undefined is not a
		// number — so we explicitly check for undefined too) are
		// treated as 0, so "1.0" and "1.0.0" compare equal.
		const rawA = partsA[i];
		const rawB = partsB[i];
		const va = rawA === undefined || Number.isNaN(rawA) ? 0 : rawA;
		const vb = rawB === undefined || Number.isNaN(rawB) ? 0 : rawB;
		if (va < vb) return -1;
		if (va > vb) return 1;
	}
	return 0;
}
