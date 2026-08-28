// Add/Edit template dialog state + handlers.
//
// Owns:
//   - dialog open/close + the row currently being edited
//   - the form fields (``trigger`` / ``expansion`` / ``matchMode``)
//   - openAddDialog / openEditDialog / saveTemplate / handleCloseDialog
//     / handleTriggerChange / handleExpansionChange / handleMatchModeChange
//
// ``saveTemplate`` reads from the dialog fields + ``templatesRef`` (so
// it can splice the new value into the latest committed list) + calls
// back into ``loadRows`` (provided by ``useTemplates``) to refresh
// after the IPC save lands.  Keeping the dialog state in its own hook
// (rather than in ``useTemplates``) means re-rendering the dialog on
// every keystroke doesn't re-run the templates-list memo.

import { useState } from "react";
import { t } from "@/i18n/i18n";

import { saveTemplates } from "../lib/storage";
import { rowsToTemplates } from "../lib/transform";
import type { Template, TemplateRow } from "../lib/types";

type CallFn = <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>;

interface UseTemplateDialogArgs {
	call: CallFn;
	showSnack: (
		message: string,
		kind: "success" | "error" | "warning" | "info",
	) => void;
	templatesRef: React.RefObject<TemplateRow[]>;
	loadRows: () => Promise<void>;
}

interface UseTemplateDialogResult {
	showDialog: boolean;
	editingTemplate: TemplateRow | null;
	trigger: string;
	expansion: string;
	matchMode: "exact" | "contains";
	openAddDialog: () => void;
	openEditDialog: (t: TemplateRow) => void;
	saveTemplate: () => Promise<void>;
	handleTriggerChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
	handleExpansionChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
	handleMatchModeChange: (v: string) => void;
	handleCloseDialog: () => void;
	/** Append a variable token (e.g. ``{today}``) to the output text —
	 *  backs the tappable variable chips in the dialog. */
	insertVariable: (token: string) => void;
}

export function useTemplateDialog({
	call,
	showSnack,
	templatesRef,
	loadRows,
}: UseTemplateDialogArgs): UseTemplateDialogResult {
	const [showDialog, setShowDialog] = useState(false);
	const [editingTemplate, setEditingTemplate] = useState<TemplateRow | null>(
		null,
	);
	const [trigger, setTrigger] = useState("");
	const [expansion, setExpansion] = useState("");
	const [matchMode, setMatchMode] = useState<"exact" | "contains">("exact");

	const openAddDialog = () => {
		setEditingTemplate(null);
		setTrigger("");
		setExpansion("");
		setMatchMode("exact");
		setShowDialog(true);
	};

	const openEditDialog = (t: TemplateRow) => {
		setEditingTemplate(t);
		setTrigger(t.trigger);
		setExpansion(t.expansion);
		setMatchMode((t.match_mode as "exact" | "contains") ?? "exact");
		setShowDialog(true);
	};

	const saveTemplate = async () => {
		if (!trigger.trim() || !expansion.trim()) {
			showSnack(t("templates.fillBothFields"), "warning");
			return;
		}
		try {
			//read from the React-state ref mirror (always
			// the latest committed list) instead of from
			// `loadTemplatesFromLocalStorage()`.  The localStorage
			// read used to race with in-flight IPC saves and could
			// disagree with what the user was seeing on screen;
			// the ref read guarantees we mutate the same list the
			// user just edited.
			const items = rowsToTemplates(templatesRef.current);
			const next: Template = {
				trigger: trigger.trim(),
				output: expansion.trim(),
				match_mode: matchMode,
			};
			if (editingTemplate) {
				items[editingTemplate.index] = next;
			} else {
				//Duplicate-trigger guard: a template is uniquely
				// identified by (trigger, match_mode). Adding a
				// second row with the same pair would create an
				// ambiguous match at substitution time (the first
				// match wins silently, so the user would think
				// their second template "doesn't work"). Reject
				// before save so the existing template is left
				// untouched and the user is told why.
				const dup = items.some(
					(it) =>
						it.trigger === next.trigger && it.match_mode === next.match_mode,
				);
				if (dup) {
					showSnack(t("templates.duplicateTrigger"), "warning");
					return;
				}
				items.push(next);
			}
			//await the IPC save BEFORE loadRows() so the
			// reload is guaranteed to see the just-saved state.
			// Previously `saveTemplates` was fire-and-forget on
			// the IPC leg, so `loadRows()` could re-fetch the
			// pre-save list and briefly render stale data.
			// NOTE: the success toast fires AFTER the await so
			// a backend rejection (e.g. output too long) does NOT
			// show a false success + error simultaneously.
			await saveTemplates(items, call);
			showSnack(
				editingTemplate
					? t("templates.updatedTemplate", { name: trigger.trim() })
					: t("templates.addedTemplate", { name: trigger.trim() }),
				"success",
			);
			setShowDialog(false);
			//reload from backend so the UI stays in sync with
			// what actually persisted (the backend may have rejected or
			// normalized entries).
			loadRows();
		} catch (err) {
			console.error(
				"[renderer:useTemplateDialog] Failed to save template",
				err,
			);
			// Surface the backend's rejection reason when available
			// (e.g. "'output' value too long in templates[3] (32913 > 1024)")
			// instead of the opaque generic "Failed to save template".
			const reason =
				err instanceof Error && err.message
					? err.message
					: t("templates.saveFailed");
			showSnack(reason, "error");
		}
	};

	const handleTriggerChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setTrigger(e.target.value);

	const handleExpansionChange = (e: React.ChangeEvent<HTMLTextAreaElement>) =>
		setExpansion(e.target.value);

	const handleMatchModeChange = (v: string) =>
		setMatchMode(v as "exact" | "contains");

	const handleCloseDialog = () => setShowDialog(false);

	// Append a variable token to the output. Insert with a leading space
	// if the output isn't empty, so tokens don't glue onto the previous
	// word.
	const insertVariable = (token: string) => {
		setExpansion((prev) =>
			prev.length > 0 && !prev.endsWith(" ")
				? `${prev} ${token}`
				: `${prev}${token}`,
		);
	};

	return {
		showDialog,
		editingTemplate,
		trigger,
		expansion,
		matchMode,
		openAddDialog,
		openEditDialog,
		saveTemplate,
		handleTriggerChange,
		handleExpansionChange,
		handleMatchModeChange,
		handleCloseDialog,
		insertVariable,
	};
}
