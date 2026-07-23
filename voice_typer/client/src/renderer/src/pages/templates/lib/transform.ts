// Pure transforms between persisted + React-side template shapes.
//
// Extracted from the former ``pages/Templates.tsx`` module-level helpers
// (toRows, rowsToTemplates, sortTemplateRows, parseImportedTemplates).
// Kept side-effect-free so the storage / hook / component layers can
// share one definition of "how to map between the backend shape and the
// row view-model" without re-implementing it (and drifting).

import { getLocale } from "@/i18n/i18n";

import { sanitizeTemplateField } from "./sanitize";
import { makeRowId } from "./storage";
import type { Template, TemplateRow, TemplateSortOrder } from "./types";
import { VARIABLES } from "./types";

export function toRows(items: Template[]): TemplateRow[] {
	return items.map((t, i) => {
		const output = t.output ?? "";
		// NEW-TS-019: track WHICH variables are used (not just the count)
		// so the UI can show them in a tooltip.  Previously only the
		// count was displayed ("2v") with no way for the user to see
		// which variables the template actually uses.
		const usedVars = VARIABLES.filter((v) => output.includes(v));
		return {
			index: i,
			id: makeRowId(),
			trigger: t.trigger ?? "",
			expansion: output,
			match_mode: t.match_mode ?? "exact",
			variables: usedVars.length,
			// Store the actual variable names for the tooltip.
			// (TemplateRow type updated below to include this.)
			used_variables: usedVars,
		};
	});
}

// CR-052: inverse of `toRows` — maps the React-state TemplateRow[]
// back to the persisted Template[] shape so `saveTemplate` and the
// `instantDeleteTemplate` undo callback can read the LATEST list from
// the `templatesRef` mirror (kept in sync by the effect below) instead
// of from `loadTemplatesFromLocalStorage()`.  Reading from the ref
// avoids two bugs:
//   1. Stale-closure: the undo callback previously closed over the
//      `tmpl.index` captured at delete time, but re-read from
//      localStorage which may have been re-written by other
//      add/edit/delete operations in the 6s undo window — so the
//      splice at the captured index landed at the WRONG position and
//      could silently reorder templates or insert duplicates.
//   2. Lost-edits: any add/edit of OTHER templates between the delete
//      and the Undo click was preserved by the localStorage read
//      (because every saveTemplates call writes localStorage), but
//      the captured `tmpl.index` was NOT re-clamped to the new list
//      length, so a shrunken list could get an out-of-bounds insert.
//      The ref-based read + clamp below matches Vocabulary.tsx's
//      D2-FIX pattern.
export function rowsToTemplates(rows: TemplateRow[]): Template[] {
	return rows.map((r) => ({
		trigger: r.trigger ?? "",
		output: r.expansion ?? "",
		match_mode: r.match_mode === "contains" ? "contains" : "exact",
	}));
}

/**
 * Sort template rows client-side.  Mirrors the History.tsx pattern —
 * the backend returns templates in insertion order (oldest first),
 * so "newest" reverses that to surface recently-added templates.
 *
 * Uses ``getLocale()`` for the A→Z / Z→A collation so accented
 * characters sort correctly in French/Spanish/German etc.
 */
export function sortTemplateRows(
	rows: TemplateRow[],
	order: TemplateSortOrder,
): TemplateRow[] {
	const locale = getLocale();
	const collator = new Intl.Collator(locale, {
		sensitivity: "base",
		numeric: true,
	});
	const sorted = [...rows];
	switch (order) {
		case "oldest":
			// insertion order = oldest first; identity.
			break;
		case "az":
			sorted.sort((a, b) => collator.compare(a.trigger ?? "", b.trigger ?? ""));
			break;
		case "za":
			sorted.sort((a, b) => collator.compare(b.trigger ?? "", a.trigger ?? ""));
			break;
		default:
			// Reverse insertion order so the most-recently-added template
			// appears at the top.
			sorted.reverse();
			break;
	}
	return sorted;
}

/**
 * Parse an imported file's text content into a Template[] array.
 * Accepts both a bare JSON array of {trigger, output, match_mode}
 * objects and the export shape ``{ templates: [...] }`` produced by
 * the Vocabulary / Templates export handlers (forward-compat).
 *
 * Throws on malformed JSON or non-array payload so the caller can
 * surface a toast.error with the parse failure reason.
 */
export function parseImportedTemplates(text: string): Template[] {
	const parsed = JSON.parse(text) as unknown;
	const arr = Array.isArray(parsed)
		? parsed
		: (parsed as { templates?: unknown })?.templates;
	if (!Array.isArray(arr)) {
		throw new Error("File does not contain a templates array");
	}
	return arr.map((t: Partial<Template>) => ({
		trigger: sanitizeTemplateField(t.trigger),
		output: sanitizeTemplateField(t.output),
		match_mode: t.match_mode === "contains" ? "contains" : "exact",
	}));
}
