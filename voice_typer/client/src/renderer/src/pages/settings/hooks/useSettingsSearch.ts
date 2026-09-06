// Settings search derivations + label-based auto-switch hook.
//
// Extracted from `pages/Settings.tsx` (page-root slimming): the search
// block was the page's largest cohesive chunk of event/derivation logic
// — one memoized "label universe" consumed by THREE derivations (the
// empty-banner sentinel, the cross-section result groups, and the
// section auto-switch effect) plus the auto-switch navigation itself.
// The page root now wires this hook and renders; the search semantics
// live here, testable in isolation.
//
// The contract that MUST survive any edit (pinned by the Settings page
// tests): ONE memoized label universe + ONE shared match predicate
// (`searchLabelMatches`) used by ALL THREE derivations, so the banner,
// the cross-section groups, and the auto-switch can never disagree.

import { useEffect, useMemo, useRef } from "react";
import { getPrewarmAndUpdatesLabels } from "@/components/settings/PrewarmAndUpdates";
import type { SettingsSectionPage } from "@/components/settings/settingsSections";
import { getSectionLabels } from "@/components/settings/settingsTabLabels";
import type { NavigateOptions } from "@/hooks/useNavigation";
import type { Page } from "@/types/ipc";

/**
 * The ONE match predicate shared by all three search derivations
 * (empty-banner sentinel, cross-section result groups, auto-switch):
 * case-insensitive substring — a label matches when the LABEL contains
 * the query. Deliberately the stricter of the two semantics that used to
 * coexist: the superstring direction (query contains a short label) was
 * applied only by the auto-switch, which let the page navigate to a
 * matching section while the empty banner claimed nothing matched. One
 * predicate = the banner, the cross-section groups, and the auto-switch
 * can never disagree, and all three stay aligned with the sections' own
 * `isVisible` row filter (also label-includes-query).
 */
export function searchLabelMatches(label: string, query: string): boolean {
	const q = query.toLowerCase().trim();
	return label.toLowerCase().includes(q);
}

/** One section page's cross-section result group (matched labels only). */
export interface OtherSectionGroup {
	sectionPage: SettingsSectionPage;
	labels: string[];
}

export interface UseSettingsSearchOptions {
	/** The active global-search query (from the useGlobalSearch store). */
	query: string;
	/** The active section page, or `null` on the Settings hub. */
	activeSection: SettingsSectionPage | null;
	/** Navigation callback (from useNavigation) used by the auto-switch. */
	navigate: (page: Page, opts?: NavigateOptions) => void;
}

export interface UseSettingsSearchReturn {
	/**
	 * The single memoized "label universe" shared by ALL THREE search
	 * derivations (auto-switch, empty-banner sentinel, cross-section
	 * groups): the translated per-section label sets with the
	 * PrewarmAndUpdates row labels folded into the Advanced page's set.
	 */
	sectionLabelsByPage: Record<SettingsSectionPage, string[]>;
	/** Empty-banner sentinel: does ANY label anywhere match the query? */
	hasAnyVisibleRow: boolean;
	/** Cross-section result groups (matches from OTHER section pages). */
	otherSectionGroups: OtherSectionGroup[];
}

/**
 * Search derivations + auto-switch for the Settings page. See the file
 * header for the extraction rationale and the one-predicate contract.
 */
export function useSettingsSearch({
	query,
	activeSection,
	navigate,
}: UseSettingsSearchOptions): UseSettingsSearchReturn {
	// The single memoized "label universe" shared by ALL THREE search
	// derivations (auto-switch below, empty-banner sentinel, cross-section
	// groups): the translated per-section label sets with the
	// PrewarmAndUpdates row labels (e.g. "Prewarm Status", "Installed
	// version", "Latest release") folded into the Advanced page's set, so
	// queries like "prewarm" / "cache" / "version" / "update" route to the
	// page where that component lives. Built ONCE per query/section change
	// instead of three times per keystroke; the helpers translate at call
	// time, so the labels reflect the active locale at the moment the user
	// types. The `activeSection` dep keeps the universe at least as fresh
	// as every consumer's own re-run schedule (it used to be re-fetched by
	// each derivation on section switches too).
	// biome-ignore lint/correctness/useExhaustiveDependencies: deliberate over-dependency — query/activeSection re-trigger this memo in sync with every consumer's own re-run schedule
	const sectionLabelsByPage = useMemo(() => {
		const sectionLabels = getSectionLabels();
		sectionLabels.settingsAdvanced = [
			...sectionLabels.settingsAdvanced,
			...getPrewarmAndUpdatesLabels(),
		];
		return sectionLabels;
	}, [query, activeSection]);

	// label-based search auto-switch (SECTION PAGES ONLY — on the hub a
	// query filters the section rows instead of yanking the user to a
	// section page mid-typing). Score each section page by counting
	// label matches (via the shared `searchLabelMatches` predicate — the
	// ONE match semantic, identical to the banner + cross-section groups)
	// and navigate to the highest-scoring one. Requires q.length >= 2 to
	// avoid jarring switches as the user types.
	//
	// When the best-matching page is DIFFERENT from the current section
	// page, navigate + carry the matched label as a settingsScrollTarget
	// rowHint so the destination can scroll to + highlight the matched
	// row. When it IS the current page, no navigation is needed — the
	// local filter predicate (`_filter_settings`) handles in-page
	// filtering.
	//
	// The very first render is skipped so a stale query left in the
	// store by a previous visit doesn't yank the user to another page
	// on mount.
	const searchNavFirstRenderRef = useRef(true);
	useEffect(() => {
		if (searchNavFirstRenderRef.current) {
			searchNavFirstRenderRef.current = false;
			return;
		}
		if (!activeSection) return;
		const q = query.toLowerCase().trim();
		if (!q || q.length < 2) return;
		let bestPage: SettingsSectionPage | null = null;
		let bestScore = 0;
		let bestLabel = "";
		for (const [sectionPage, labels] of Object.entries(sectionLabelsByPage)) {
			for (const label of labels) {
				if (!searchLabelMatches(label, q)) continue;
				const score = label.length; // prefer the longest (most specific) match
				if (score > bestScore) {
					bestScore = score;
					bestPage = sectionPage as SettingsSectionPage;
					bestLabel = label;
				}
			}
		}
		if (bestPage && bestScore > 0 && bestPage !== activeSection) {
			// Cross-page navigation — carry the matched label as a
			// rowHint so the destination page can scroll + ring.
			navigate(bestPage, {
				settingsScrollTarget: { rowHint: bestLabel },
			});
		}
	}, [query, activeSection, navigate, sectionLabelsByPage]);

	// empty-state sentinel — derived purely from `query` via
	// useMemo: if no section label (across all section pages + the
	// PrewarmAndUpdates rows) matches the query, the empty banner is
	// shown on section pages. Consumes the SAME memoized label universe
	// (`sectionLabelsByPage`) and the SAME `searchLabelMatches` predicate
	// as the auto-switch effect and the cross-section groups, so the
	// banner can never contradict a navigation. (The hub renders its own
	// empty state inside SettingsHub.)
	const hasAnyVisibleRow = useMemo(() => {
		if (!query.trim()) return true;
		const q = query.toLowerCase().trim();
		return Object.values(sectionLabelsByPage)
			.flat()
			.some((label) => searchLabelMatches(label, q));
	}, [query, sectionLabelsByPage]);

	// "Results from other section pages" (search grouping): matches the
	// SAME memoized label universe (`sectionLabelsByPage`) + the SAME
	// `searchLabelMatches` predicate as the empty-state sentinel and the
	// auto-switch, but keeps every match from every OTHER section page
	// (the active page's own matches are filtered inline by the
	// sections). Each entry navigates to its page with a rowHint so the
	// destination scrolls to + rings the matched row (the proven search
	// deep-link path). Only rendered on section pages — the hub's rows
	// already list their matched labels inline.
	const otherSectionGroups = useMemo(() => {
		if (!activeSection || !query.trim()) return [];
		const q = query.toLowerCase().trim();
		return Object.entries(sectionLabelsByPage)
			.filter(([sectionPage]) => sectionPage !== activeSection)
			.map(([sectionPage, labels]) => ({
				sectionPage: sectionPage as SettingsSectionPage,
				// Different section titles can render the same translated
				// word — dedupe so a match produces ONE chip per unique
				// label (and unique React keys).
				labels: [...new Set(labels)].filter((label) =>
					searchLabelMatches(label, q),
				),
			}))
			.filter((g) => g.labels.length > 0);
	}, [query, activeSection, sectionLabelsByPage]);

	return { sectionLabelsByPage, hasAnyVisibleRow, otherSectionGroups };
}
