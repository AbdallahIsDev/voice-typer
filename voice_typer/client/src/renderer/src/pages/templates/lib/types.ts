// Shared types + constants for the templates package.
//
// Extracted from the former monolithic ``pages/Templates.tsx`` so the
// storage / transform / hook / component modules can reference a single
// canonical definition of ``Template`` / ``TemplateRow`` without
// re-declaring it (which previously led to drift between the persisted
// shape and the React-state shape).

/** Template-variable tokens recognised in template output. */
export const VARIABLES = [
	"{today}",
	"{now}",
	"{clipboard}",
	"{username}",
] as const;

/** Persisted template shape (the form stored in backend + localStorage). */
export interface Template {
	trigger: string;
	output: string;
	match_mode: "exact" | "contains";
}

/**
 * React-side view of a template.  Enriches the persisted shape with:
 *  - ``index``: position within the persisted list (canonical reference
 *    for edit/delete — the backend stores templates as a positional
 *    array, so the index is the canonical reference for edits).
 *  - ``id``: stable client-side UUID generated when the row is
 *    materialised from the backend list.  Used as the React key so list
 *    re-orders (sort, search filter, add/edit/delete) don't reuse DOM
 *    nodes across different templates — the previous ``key={row.index}``
 *    caused input focus and animation state to leak between rows when
 *    the list order changed (e.g. after a sort or undo restore).
 *  - ``expansion``: alias for ``output`` (the UI label).
 *  - ``variables``: count of variable tokens in the output.
 *  - ``used_variables``: the actual variable names (NEW-TS-019 — so the
 *    UI can show them in a tooltip instead of just a count).
 */
export interface TemplateRow {
	index: number;
	id: string;
	trigger: string;
	expansion: string;
	match_mode: string;
	variables: number;
	used_variables: readonly string[];
}

export type TemplateSortOrder = "newest" | "oldest" | "az" | "za";
