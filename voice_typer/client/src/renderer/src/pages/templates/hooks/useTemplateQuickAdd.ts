// Inline "quick add" row state + save logic for the Templates page.
//
// Mirrors ``useVocabularyQuickAdd`` — the inline row lives at the top of
// the templates list so create stays in-place (the list is visible the
// whole time; no modal). Edits still flow through ``useTemplateDialog``
// + ``TemplateDialog`` — the inline pattern is intentionally ONLY for
// add (inline quick-add). The fields shown inline are the two simplified ones the
// user fills in 99% of the time (trigger + output). Match mode defaults
// to "exact" and can be changed later via Edit.
//
// Save logic: trim both fields, refuse duplicates by (trigger,
// match_mode) (same guard as ``useTemplateDialog.saveTemplate``), append
// to the latest committed list (read from ``templatesRef`` to avoid
// stale-closure races with in-flight IPC writes), persist via
// ``saveTemplates`` (the same atomic write the dialog uses), then refresh
// via ``loadRows`` so the UI stays in sync with what the backend
// actually persisted (the backend may reject or normalize entries).

import { useCallback, useState } from "react";
import { t } from "@/i18n/i18n";

import { saveTemplates } from "../lib/storage";
import { rowsToTemplates } from "../lib/transform";
import type { Template, TemplateRow } from "../lib/types";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseTemplateQuickAddArgs {
	call: CallFn;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	templatesRef: React.RefObject<TemplateRow[]>;
	loadRows: () => Promise<void>;
}

interface UseTemplateQuickAddResult {
	open: boolean;
	trigger: string;
	expansion: string;
	matchMode: "exact" | "contains";
	error: string | null;
	openQuickAdd: () => void;
	closeQuickAdd: () => void;
	setTrigger: (v: string) => void;
	setExpansion: (v: string) => void;
	saveQuickAdd: () => Promise<void>;
}

export function useTemplateQuickAdd({
	call,
	showSnack,
	templatesRef,
	loadRows,
}: UseTemplateQuickAddArgs): UseTemplateQuickAddResult {
	const [open, setOpen] = useState(false);
	const [trigger, setTrigger] = useState("");
	const [expansion, setExpansion] = useState("");
	const [matchMode, setMatchMode] = useState<"exact" | "contains">("exact");
	// Inline error shown under the quick-add inputs (e.g. duplicate
	// (trigger, match_mode) pair). Cleared on any field change or on
	// open so a stale rejection doesn't linger.
	const [error, setError] = useState<string | null>(null);

	// Wrappers that clear the inline error on edit so a stale rejection
	// doesn't linger after the user changes the inputs. Mirrors
	// ``useVocabularyQuickAdd``'s handleTriggerChange /
	// handleReplacementChange.
	const handleTriggerChange = useCallback((v: string) => {
		setTrigger(v);
		setError(null);
	}, []);
	const handleExpansionChange = useCallback((v: string) => {
		setExpansion(v);
		setError(null);
	}, []);

	const openQuickAdd = () => {
		setTrigger("");
		setExpansion("");
		setMatchMode("exact");
		setError(null);
		setOpen(true);
	};

	const closeQuickAdd = () => setOpen(false);

	const saveQuickAdd = async () => {
		const trimmedTrigger = trigger.trim();
		const trimmedExpansion = expansion.trim();
		if (!trimmedTrigger || !trimmedExpansion) {
			showSnack(t("templates.fillBothFields"), "warning");
			return;
		}
		try {
			// Read from the React-state ref mirror (always the latest
			// committed list) instead of from
			// ``loadTemplatesFromLocalStorage()``. The localStorage read
			// used to race with in-flight IPC saves and could disagree
			// with what the user was seeing on screen; the ref read
			// guarantees we mutate the same list the user just saw.
			const items = rowsToTemplates(templatesRef.current);
			const next: Template = {
				trigger: trimmedTrigger,
				output: trimmedExpansion,
				match_mode: matchMode,
			};
			// Duplicate-trigger guard: a template is uniquely identified
			// by (trigger, match_mode). Same rule as
			// ``useTemplateDialog.saveTemplate`` — kept inline rather
			// than factored out because the call sites differ in shape
			// (dialog pre-fills fields from the row being edited; the
			// inline form is always a fresh add with default match mode).
			const dup = items.some(
				(it) =>
					it.trigger === next.trigger && it.match_mode === next.match_mode,
			);
			if (dup) {
				setError(t("templates.duplicateTrigger"));
				return;
			}
			items.push(next);
			// Await the IPC save BEFORE loadRows() so the reload is
			// guaranteed to see the just-saved state. Same reasoning as
			// ``useTemplateDialog.saveTemplate``.
			await saveTemplates(items, call);
			setOpen(false);
			setError(null);
			showSnack(
				t("templates.addedTemplate", { name: trimmedTrigger }),
				"success",
			);
			await loadRows();
		} catch (err) {
			console.error(
				"[renderer:useTemplateQuickAdd] Failed to save template",
				err,
			);
			showSnack(t("templates.saveFailed"), "error");
		}
	};

	return {
		open,
		trigger,
		expansion,
		matchMode,
		error,
		openQuickAdd,
		closeQuickAdd,
		setTrigger: handleTriggerChange,
		setExpansion: handleExpansionChange,
		saveQuickAdd,
	};
}
