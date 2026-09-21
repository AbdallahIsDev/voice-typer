import type { StatTrend } from "@/components/dashboard/StatCard";

/**
 * Percentage trend of `cur` against `prev`.
 *
 * `null` when there is no usable baseline (missing or non-positive), so the
 * stat card omits its trend indicator instead of inventing a comparison.
 */
export function computeTrend(
	cur: number,
	prev: number | null | undefined,
): StatTrend | null {
	if (prev === null || prev === undefined || prev <= 0) return null;
	const delta = cur - prev;
	if (delta === 0) return { pct: 0, up: true };
	return { pct: Math.abs(Math.round((delta / prev) * 100)), up: delta > 0 };
}
