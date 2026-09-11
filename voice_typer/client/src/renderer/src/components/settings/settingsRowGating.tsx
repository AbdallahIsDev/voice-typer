// settingsRowGating, shared primitives for the Settings sections'
// in-section search filtering.
//
// The page-level search predicate (pages/Settings.tsx `_filter_settings`,
// handed to every section as the `isVisible` prop) matches a row when
// its label, info, OR the section title contains the query. Sections
// use it in two places, previously copy-pasted per section:
//
//   1. a section-level "does ANY row match?" check that hides the
//      whole card when nothing matches, and
//   2. a per-row wrap so a query that matches ONE row hides the OTHER
//      rows of the same section (previously several sections showed
//      the entire card whenever any single row matched, which defeated
//      the purpose of in-section search, see AudioSettingsSection's
//      comment on its per-row gates).
//
// This module owns both halves once:
//
//   - `anyRowVisible` replaces the per-section
//     `items.some((item) => isVisible(item.label, item.info, title))`
//     blocks.
//   - `GatedSettingRow` wraps `SettingRow` with the per-row predicate
//     call. It is a slot-props wrapper (not a descriptor registry à la
//     audioFilterRowDescriptors) because the sections' rows are
//     heterogeneous, HotkeyPickers with capture callbacks, Inputs with
//     draft state, segmented controls, sliders, a registry would need
//     a per-row render slot anyway.
//
// Rows whose rendered tooltip text differs from the searchable
// description (e.g. Recording renders the `*InfoSearch` key while its
// search registry uses the `*Info` key for one row) pass `searchInfo`
// explicitly; for everyone else `searchInfo` defaults to `info`, so
// the predicate and the tooltip see the same string.

import type { ReactNode } from "react";
import { SettingRow } from "@/components/common/SettingRow";
import type { IsVisibleFn } from "./types";

/**
 * One searchable row: the visible label plus the optional info string
 * the search predicate matches against. Sections build their
 * per-section registries (the items arrays) from the same strings they
 * render.
 */
export interface SearchableRow {
	label: string;
	info?: string;
}

/**
 * Section-level visibility check: does any row (or the section title
 * itself, when the caller includes it as a row or checks it
 * separately) match the active search query?
 */
export function anyRowVisible(
	isVisible: IsVisibleFn,
	sectionTitle: string,
	rows: readonly SearchableRow[],
): boolean {
	return rows.some((row) => isVisible(row.label, row.info, sectionTitle));
}

interface GatedSettingRowProps {
	/** Search-filter predicate from the Settings page. */
	isVisible: IsVisibleFn;
	/**
	 * The section title, the predicate's third argument, so a query
	 * that matches the heading surfaces every row of the section. MUST
	 * be the same constant handed to `<SettingsSection title>`.
	 */
	sectionTitle: string;
	/** Rendered row label, also the predicate's primary match target. */
	label: string;
	/** Rendered tooltip text. Also the predicate's info when `searchInfo` is omitted. */
	info?: string;
	/**
	 * String the search predicate matches against, for rows whose
	 * searchable description differs from the rendered tooltip.
	 * Defaults to `info`.
	 */
	searchInfo?: string;
	children: ReactNode;
}

/**
 * A `SettingRow` that renders only when the search predicate accepts
 * it, the per-row half of in-section search filtering. With an empty
 * query (predicate always true) it renders exactly what `SettingRow`
 * would, so the no-search behavior is unchanged.
 */
export function GatedSettingRow({
	isVisible,
	sectionTitle,
	label,
	info,
	searchInfo,
	children,
}: GatedSettingRowProps) {
	if (!isVisible(label, searchInfo ?? info, sectionTitle)) return null;
	return (
		<SettingRow label={label} info={info}>
			{children}
		</SettingRow>
	);
}
