// Add/Edit vocabulary dialog state + handlers.
//
// Owns:
//   - dialog open/close + the row currently being edited
//   - the form fields (``trigger`` / ``replacement`` / ``category``)
//   - openAddDialog / openEditDialog / saveEntry / handleCloseDialog
//     / handleTriggerChange / handleReplacementChange
//
// ``saveEntry`` reads from the dialog fields + the latest ``entries``
// (provided by ``useVocabulary``) so it can splice the edited entry in
// place (preserving its existing ``_id`` — React re-uses the DOM node
// so input focus / animation state isn't lost) or append a new entry
// (with a fresh UUID).  After the IPC save lands it calls
// ``persistVocabulary`` + ``setEntries`` (both provided by
// ``useVocabulary``) to commit the change.

import { useState } from "react";
import { t } from "@/i18n/i18n";
import type { VocabularyEntry } from "@/types/ipc";

import { detectCategory } from "../lib/categories";
import { makeEntryId, type VocabRow } from "../lib/transform";

interface UseVocabularyDialogArgs {
	entries: VocabRow[];
	setEntries: (entries: VocabRow[]) => void;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseVocabularyDialogResult {
	showDialog: boolean;
	editingEntry: VocabRow | null;
	trigger: string;
	replacement: string;
	category: string;
	setCategory: (c: string) => void;
	openAddDialog: () => void;
	openEditDialog: (entry: VocabRow) => void;
	saveEntry: () => Promise<void>;
	handleTriggerChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	handleReplacementChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	handleCloseDialog: () => void;
}

export function useVocabularyDialog({
	entries,
	setEntries,
	persistVocabulary,
	showSnack,
}: UseVocabularyDialogArgs): UseVocabularyDialogResult {
	const [showDialog, setShowDialog] = useState(false);
	const [editingEntry, setEditingEntry] = useState<VocabRow | null>(null);
	const [trigger, setTrigger] = useState("");
	const [replacement, setReplacement] = useState("");
	//explicit category selection.
	const [category, setCategory] = useState<string>("auto");

	const openAddDialog = () => {
		setEditingEntry(null);
		setTrigger("");
		setReplacement("");
		setCategory("auto");
		setShowDialog(true);
	};

	const openEditDialog = (entry: VocabRow) => {
		setEditingEntry(entry);
		setTrigger(entry.original);
		//pre-select the entry's existing category.
		setCategory(entry.category || "auto");
		setReplacement(entry.correction);
		setShowDialog(true);
	};

	const saveEntry = async () => {
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
			let updated: VocabRow[];
			if (editingEntry) {
				// Preserve the existing ``_id`` so React doesn't remount
				// the row (which would lose input focus / animation state).
				updated = entries.map((e) =>
					e === editingEntry
						? {
								_id: e._id,
								category: resolvedCategory as VocabularyEntry["category"],
								original: trimmedTrigger,
								correction: r,
							}
						: e,
				);
			} else {
				// Duplicate-detection — refuse to append a new entry
				// whose (original, category) pair already exists in the
				// list. Without this, the user could silently accumulate
				// duplicate triggers (one per category) which the backend
				// would happily accept but the UI would render as
				// visually-identical rows. Edits are exempt (the user is
				// modifying an existing entry, not adding a duplicate).
				const isDuplicate = entries.some(
					(it) =>
						it.original === trimmedTrigger && it.category === resolvedCategory,
				);
				if (isDuplicate) {
					showSnack(t("vocabulary.duplicateOriginal"), "warning");
					return;
				}
				// New entry — generate a fresh UUID.
				updated = [
					...entries,
					{
						_id: makeEntryId(),
						category: resolvedCategory as VocabularyEntry["category"],
						original: trimmedTrigger,
						correction: r,
					},
				];
			}
			await persistVocabulary(updated);
			setEntries(updated);
			setShowDialog(false);
			showSnack(
				editingEntry
					? t("vocabulary.updatedEntry", {
							original: trimmedTrigger,
							correction: r,
						})
					: t("vocabulary.addedEntry", {
							original: trimmedTrigger,
							correction: r,
						}),
				"success",
			);
		} catch {
			showSnack(t("vocabulary.saveFailed"), "error");
		}
	};

	const handleTriggerChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setTrigger(e.target.value);

	const handleReplacementChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setReplacement(e.target.value);

	const handleCloseDialog = () => setShowDialog(false);

	return {
		showDialog,
		editingEntry,
		trigger,
		replacement,
		category,
		setCategory,
		openAddDialog,
		openEditDialog,
		saveEntry,
		handleTriggerChange,
		handleReplacementChange,
		handleCloseDialog,
	};
}
