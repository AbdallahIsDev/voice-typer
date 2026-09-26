import Fuse from "fuse.js";
import { useMemo } from "react";

// Shared fuzzy-search source of truth (fuse.js won over match-sorter:
// threshold + result-limit + deterministic score/refIndex ordering map
// directly onto Fuse options, while match-sorter offers no threshold
// dial for typo strictness).
export const FUZZY_THRESHOLD = 0.35;
export const FUZZY_RESULT_LIMIT = 50;

interface FuzzyDoc<T> {
	item: T;
	texts: string[];
}

function baseOptions() {
	return {
		isCaseSensitive: false,
		shouldSort: true,
		ignoreLocation: true,
		threshold: FUZZY_THRESHOLD,
		minMatchCharLength: 1,
		keys: ["texts"] as string[],
	};
}

// Single haystack predicate with exact-substring priority so existing
// UX never reshuffles: substring hits always match, Fuse only adds
// typo-tolerant extras below the threshold.
export function fuzzyContains(haystack: string, query: string): boolean {
	const q = query.trim();
	if (!q) return true;
	if (haystack.toLowerCase().includes(q.toLowerCase())) return true;
	const fuse = new Fuse([{ v: haystack }], {
		isCaseSensitive: false,
		shouldSort: true,
		ignoreLocation: true,
		threshold: FUZZY_THRESHOLD,
		minMatchCharLength: 1,
		keys: ["v"],
	});
	return fuse.search(q, { limit: 1 }).length > 0;
}

// List filter with exact matches first (stable input order), then Fuse
// extras by score. Empty query returns the input untouched; non-empty
// results are capped so long lists stay responsive.
export function filterFuzzy<T>(
	items: readonly T[],
	query: string,
	getTexts: (item: T) => string[],
): T[] {
	const q = query.trim();
	if (!q) return [...items];
	const ql = q.toLowerCase();
	const exact: T[] = [];
	const rest: T[] = [];
	for (const item of items) {
		const texts = getTexts(item);
		let hit = false;
		for (const text of texts) {
			if (text.toLowerCase().includes(ql)) {
				hit = true;
				break;
			}
		}
		if (hit) exact.push(item);
		else rest.push(item);
	}
	if (rest.length === 0) return exact.slice(0, FUZZY_RESULT_LIMIT);
	const docs: Array<FuzzyDoc<T>> = rest.map((item) => ({
		item,
		texts: getTexts(item),
	}));
	const fuse = new Fuse(docs, { ...baseOptions() });
	const fuzzyHits = fuse
		.search(q, { limit: FUZZY_RESULT_LIMIT })
		.map((r) => r.item.item);
	const seen = new Set(exact);
	const combined: T[] = [...exact];
	for (const item of fuzzyHits) {
		if (seen.has(item)) continue;
		seen.add(item);
		combined.push(item);
		if (combined.length >= FUZZY_RESULT_LIMIT) break;
	}
	return combined;
}

// Memoized hook form for component/hook call sites.
export function useFuzzyFilter<T>(
	items: readonly T[],
	query: string,
	getTexts: (item: T) => string[],
): T[] {
	const textsKey = items;
	// getTexts is inline at call sites; its identity churn would
	// defeat the memo, so callers pass stable field readers.
	// biome-ignore lint/correctness/useExhaustiveDependencies: getTexts intentionally omitted, callers use inline field accessors with stable semantics
	return useMemo(
		() => filterFuzzy(textsKey, query, getTexts),
		[textsKey, query],
	);
}
