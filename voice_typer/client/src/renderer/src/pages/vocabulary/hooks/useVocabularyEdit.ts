// Inline vocabulary EDIT state + handlers.
//
// The ADD path lives in the inline quick-add row
// (``useVocabularyQuickAdd``) and the EDIT path uses the SAME inline
// row treatment (``VocabInlineForm`` rendered in place of the edited
// row) — the old edit MODAL (VocabDialog) was removed so create and
// modify are one consistent pattern.
//
// Owns:
//   - the row currently being edited (``editingEntry``)
//   - the form fields (``trigger`` / ``replacement`` / ``category``)
//   - openEdit / saveEdit / closeEdit / handleTriggerChange
//     / handleReplacementChange
//
// ``saveEdit`` reads from the form fields + the latest ``entries``
// (provided by ``useVocabulary``) so it can splice the edited entry in
// place (preserving its existing ``_id`` — React re-uses the DOM node
// so input focus / animation state isn't lost). After the IPC save
// lands it calls ``persistVocabulary`` + ``setEntries`` (both provided
// by ``useVocabulary``) to commit the change.

import { useState } from "react";
import { t } from "@/i18n/i18n";

import { detectCategory } from "../lib/categories";
import type { VocabRow } from "../lib/transform";
import { isDuplicateEntryError } from "./useVocabularyQuickAdd";

interface UseVocabularyEditArgs {
	entries: VocabRow[];
	setEntries: (entries: VocabRow[]) => void;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseVocabularyEditResult {
	/** True while an entry is being edited inline. */
	isEditing: boolean;
	/** The row currently being edited (renders the inline form in its place). */
	editingEntry: VocabRow | null;
	trigger: string;
	replacement: string;
	openEdit: (entry: VocabRow) => void;
	saveEdit: () => Promise<void>;
	handleTriggerChange: (v: string) => void;
	handleReplacementChange: (v: string) => void;
	closeEdit: () => void;
}

export function useVocabularyEdit({
	entries,
	setEntries,
	persistVocabulary,
	showSnack,
}: UseVocabularyEditArgs): UseVocabularyEditResult {
	const [editingEntry, setEditingEntry] = useState<VocabRow | null>(null);
	const [trigger, setTrigger] = useState("");
	const [replacement, setReplacement] = useState("");
	// Category is NOT shown in the form (the page is a flat two-column
	// list) — but it must be preserved on save so the backend bucket
	// never changes behind the user's back. Initialised from the entry
	// being edited; "auto" resolves via detectCategory.
	const [category, setCategory] = useState<string>("auto");

	const openEdit = (entry: VocabRow) => {
		setEditingEntry(entry);
		setTrigger(entry.original);
		// Preserve the entry's existing bucket (don't re-detect — an
		// edit shouldn't silently re-categorize the entry).
		setCategory(entry.category || "auto");
		setReplacement(entry.correction);
	};

	const saveEdit = async () => {
		const trimmedTrigger = trigger.trim();
		const r = replacement.trim();
		if (!trimmedTrigger || !r) {
			showSnack(t("vocabulary.fillBothFields"), "warning");
			return;
		}
		//use the explicit category if the user picked one.
		const resolvedCategory =
			category === "auto" ? detectCategory(trimmedTrigger) : category;
		try {
			// Edit path only — splice the edited entry in place, keeping
			// its ``_id`` so React doesn't remount the row (which would
			// lose input focus / animation state). An edit that changes
			// the wrong phrase to one that ALREADY EXISTS is blocked by
			// the backend (client.duplicate_entry) — the matcher keys on
			// the wrong phrase, so two entries sharing it would silently
			// collide.
			const updated: VocabRow[] = entries.map((e) =>
				e === editingEntry
					? {
							_id: e._id,
							category: resolvedCategory as VocabRow["category"],
							original: trimmedTrigger,
							correction: r,
						}
					: e,
			);
			await persistVocabulary(updated);
			setEntries(updated);
			setEditingEntry(null);
			showSnack(
				t("vocabulary.updatedEntry", {
					original: trimmedTrigger,
					correction: r,
				}),
				"success",
			);
		} catch (err) {
			if (isDuplicateEntryError(err)) {
				showSnack(t("vocabulary.duplicateOriginal"), "warning");
				return;
			}
			showSnack(t("vocabulary.saveFailed"), "error");
		}
	};

	const handleTriggerChange = (v: string) => setTrigger(v);

	const handleReplacementChange = (v: string) => setReplacement(v);

	const closeEdit = () => setEditingEntry(null);

	return {
		isEditing: editingEntry !== null,
		editingEntry,
		trigger,
		replacement,
		openEdit,
		saveEdit,
		handleTriggerChange,
		handleReplacementChange,
		closeEdit,
	};
}
