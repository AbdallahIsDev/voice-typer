// Inline "quick add" row state + save logic for the Vocabulary page.
//
// Replaces the disconnected Add-Entry modal (the list stays visible
// while adding). The row lives at the top of the list: Wrong-phrase +
// correct-phrase inputs and Save/Cancel — the simplified two-field
// flow (no category picker; the backend bucket is auto-detected).
//
// Save logic: trim both fields, resolve the category (auto-detect),
// refuse duplicate wrong→correct pairs via `findDuplicate`, append
// with a fresh UUID, persist, and toast.

import { useCallback, useState } from "react";
import { t } from "@/i18n/i18n";

import { detectCategory, findDuplicate } from "../lib/categories";
import { makeEntryId, type VocabRow } from "../lib/transform";

/** Error code the backend stamps on a rejected duplicate write
 * (ErrorCodes.DUPLICATE_ENTRY in server/ipc/validation.py). */
const DUPLICATE_ENTRY_CODE = "client.duplicate_entry";

export function isDuplicateEntryError(err: unknown): boolean {
	return (
		err instanceof Error &&
		err.message !== undefined &&
		(err as { code?: string }).code === DUPLICATE_ENTRY_CODE
	);
}

interface UseVocabularyQuickAddArgs {
	entries: VocabRow[];
	setEntries: (entries: VocabRow[]) => void;
	persistVocabulary: (updated: VocabRow[]) => Promise<void>;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
}

interface UseVocabularyQuickAddResult {
	open: boolean;
	trigger: string;
	replacement: string;
	error: string | null;
	openQuickAdd: (prefill?: {
		original: string;
		correction: string;
		category?: string;
	}) => void;
	closeQuickAdd: () => void;
	setTrigger: (v: string) => void;
	setReplacement: (v: string) => void;
	saveQuickAdd: () => Promise<void>;
}

export function useVocabularyQuickAdd({
	entries,
	setEntries,
	persistVocabulary,
	showSnack,
}: UseVocabularyQuickAddArgs): UseVocabularyQuickAddResult {
	const [open, setOpen] = useState(false);
	const [trigger, setTrigger] = useState("");
	const [replacement, setReplacement] = useState("");
	// Inline error shown under the quick-add inputs (e.g. "This
	// correction already exists" when the backend rejects the write
	// with ``client.duplicate_entry``). Cleared on any field change
	// or on open.
	const [error, setError] = useState<string | null>(null);
	const [category, setCategory] = useState("auto");

	// Wrappers that clear the inline error on edit so a stale
	// rejection doesn't linger after the user changes the inputs.
	const handleTriggerChange = useCallback((v: string) => {
		setTrigger(v);
		setError(null);
	}, []);
	const handleReplacementChange = useCallback((v: string) => {
		setReplacement(v);
		setError(null);
	}, []);

	const openQuickAdd: UseVocabularyQuickAddResult["openQuickAdd"] = (
		prefill,
	) => {
		setTrigger(prefill?.original ?? "");
		setReplacement(prefill?.correction ?? "");
		setError(null);
		// Category is hidden from the UI — a blank add auto-detects its
		// bucket; a prefill (duplicate) keeps the source entry's bucket.
		setCategory(prefill?.category ?? "auto");
		setOpen(true);
	};

	const closeQuickAdd = () => setOpen(false);

	const saveQuickAdd = async () => {
		const trimmedTrigger = trigger.trim();
		const r = replacement.trim();
		if (!trimmedTrigger || !r) {
			showSnack(t("vocabulary.fillBothFields"), "warning");
			return;
		}
		const resolvedCategory =
			category === "auto" ? detectCategory(trimmedTrigger) : category;
		try {
			// Convenience pre-check (case-insensitive wrong phrase, same
			// rule as the backend). The AUTHORITATIVE check runs in the
			// backend write path (save_vocabulary_with_diff) — this only
			// gives instant feedback before the round-trip.
			const isDuplicate = findDuplicate(entries, trimmedTrigger);
			if (isDuplicate) {
				setError(t("vocabulary.duplicateOriginal"));
				return;
			}
			const updated: VocabRow[] = [
				...entries,
				{
					_id: makeEntryId(),
					category: resolvedCategory as VocabRow["category"],
					original: trimmedTrigger,
					correction: r,
				},
			];
			await persistVocabulary(updated);
			setEntries(updated);
			setOpen(false);
			setError(null);
			showSnack(
				t("vocabulary.addedEntry", {
					original: trimmedTrigger,
					correction: r,
				}),
				"success",
			);
		} catch (err) {
			// Backend duplicate rejection: the save_vocabulary write was
			// refused (client.duplicate_entry) — surface the inline
			// "already exists" message and DO NOT add the row. This is
			// the authoritative path (covers races and cross-case
			// collisions the pre-check can't see).
			if (isDuplicateEntryError(err)) {
				setError(t("vocabulary.duplicateOriginal"));
				return;
			}
			showSnack(t("vocabulary.saveFailed"), "error");
		}
	};

	return {
		open,
		trigger,
		replacement,
		error,
		openQuickAdd,
		closeQuickAdd,
		setTrigger: handleTriggerChange,
		setReplacement: handleReplacementChange,
		saveQuickAdd,
	};
}
