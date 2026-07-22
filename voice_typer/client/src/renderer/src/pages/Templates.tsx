import {
	Add01Icon,
	AlertCircleIcon,
	Delete01Icon,
	File02Icon,
	PencilEdit02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import ExportFormatMenu from "@/components/common/ExportFormatMenu";
import { Modal, ModalFooter } from "@/components/common/Modal";
import PageHeading from "@/components/common/PageHeading";
import { SearchField } from "@/components/common/SearchField";
import { EmptyState } from "@/components/feedback/EmptyState";
import { InfoTooltip } from "@/components/feedback/InfoTooltip";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { usePython } from "@/hooks/usePython";
import { showUndoableToast, useSnackbar } from "@/hooks/useSnackbar";
import { getLocale, t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

// NEW-UX-008: Templates are persisted by the Python backend to
// ``voice-typer-templates.json`` in the user's voice-typer config
// directory (``~/.voice-typer`` on POSIX, ``%APPDATA%\voice-typer``
// on Windows).  This file survives Electron userData resets and
// reinstalls, so templates are no longer lost on app data wipe.
//
// localStorage is now used ONLY as a one-time migration source: if
// the backend has no templates but localStorage does (e.g. user
// upgrades from a previous build), we push the localStorage data to
// the backend on first load and then localStorage is no longer read.
const STORAGE_KEY = "templates_data";
const MIGRATION_FLAG_KEY = "templates_migrated_to_backend";

const VARIABLES = ["{today}", "{now}", "{clipboard}", "{username}"] as const;

interface Template {
	trigger: string;
	output: string;
	match_mode: "exact" | "contains";
}

interface TemplateRow {
	/**
	 * Position of the template within the persisted list.  Used by the
	 * edit/delete handlers to splice the right element when saving back
	 * to the backend (the backend stores templates as a positional
	 * array, so the index is the canonical reference for edits).
	 */
	index: number;
	/**
	 * Stable client-side UUID generated when the row is materialised
	 * from the backend list.  Used as the React key so list re-orders
	 * (sort, search filter, add/edit/delete) don't reuse DOM nodes
	 * across different templates — the previous `key={row.index}`
	 * caused input focus and animation state to leak between rows
	 * when the list order changed (e.g. after a sort or undo restore).
	 */
	id: string;
	trigger: string;
	expansion: string;
	match_mode: string;
	variables: number;
	// NEW-TS-019: the actual variable names used in the template output,
	// so the UI can show them in a tooltip instead of just a count.
	used_variables: readonly string[];
}

function loadTemplatesFromLocalStorage(): Template[] {
	try {
		const raw = localStorage.getItem(STORAGE_KEY) ?? "[]";
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		// SEC-027: sanitize each template field on load. localStorage is a
		// stored-XSS vector IF any future code path renders a template value
		// via dangerouslySetInnerHTML. We strip angle brackets and null
		// bytes from trigger + output so even a malicious payload injected
		// into localStorage (by another process, a browser extension, or a
		// prior compromised session) cannot contain HTML markup. Plain text
		// templates are unaffected. The variables list still scans the
		// sanitized output for {today}/{now}/{clipboard}/{username}.
		return parsed.map((t: Partial<Template>) => ({
			trigger: _sanitizeTemplateField(t.trigger),
			output: _sanitizeTemplateField(t.output),
			match_mode: t.match_mode === "contains" ? "contains" : "exact",
		}));
	} catch {
		return [];
	}
}

/**
 * NEW-UX-008: load templates from the Python backend.  Falls back to
 * localStorage on IPC failure (e.g. backend not yet started) so the
 * page remains usable during startup.
 *
 * NF-R10-8: previously this function returned `[]` for BOTH "no
 * templates exist" (valid empty array from backend) AND "the backend
 * returned malformed data" (null/undefined result, or a `templates`
 * field that wasn't an array). That collapsed two very different
 * states into one empty list, hiding genuine load failures from the
 * user. Now we throw on genuine failure and only return `[]` when the
 * backend explicitly reported an empty (but valid) template list.
 */
async function loadTemplatesFromBackend(
	callFn: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<Template[]> {
	const result = await callFn<{ templates?: Template[] } | Template[]>(
		"get_templates",
	);
	// The IPC layer may return either { templates: [...] } or a bare
	// array — accept both for forward/backward compat.
	const arr = Array.isArray(result) ? result : result?.templates;
	if (!Array.isArray(arr)) {
		// Genuine failure: the backend returned a non-array shape (null,
		// undefined, or a malformed object). Distinguish from a valid
		// empty list (arr === []) so the caller can surface a load error
		// instead of treating this as "no templates exist".
		throw new Error(
			"Backend returned malformed templates payload (expected array)",
		);
	}
	return arr.map((t: Partial<Template>) => ({
		trigger: _sanitizeTemplateField(t.trigger),
		output: _sanitizeTemplateField(t.output),
		match_mode: t.match_mode === "contains" ? "contains" : "exact",
	}));
}

/**
 * SEC-027: strip characters that would allow HTML/script injection.
 * Removes `<`, `>`, `\u0000`, and attribute-delimiter quotes. Plain
 * text and template variables ({today}, {clipboard}, etc.) are
 * preserved. The result is safe to render even via
 * dangerouslySetInnerHTML (though we still avoid that pattern).
 */
function _sanitizeTemplateField(value: unknown): string {
	if (typeof value !== "string") return "";
	// Use String.fromCharCode(0) to avoid the no-control-regex lint rule
	// (a literal /\u0000/ in source would trigger it). The NUL byte is
	// a real XSS vector because browsers truncate attribute strings at
	// NUL — injecting `value="\u0000onload=alert(1)"` would let the
	// `onload=alert(1)` portion execute as an attribute.
	const nul = String.fromCharCode(0);
	return value
		.replace(/</g, "")
		.replace(/>/g, "")
		.replace(/"/g, "")
		.replace(/'/g, "")
		.split(nul)
		.join("");
}

// #6: saveTemplates now accepts an optional callFn for IPC persistence.
// Add/edit paths pass the IPC call function so the server is notified.
// Delete path also passes callFn so the server stays in sync.
//
// NEW-UX-008: backend persistence is now functional (previously the
// IPC save was a no-op because the Config dataclass had no
// templates_data field).  We still mirror to localStorage as a
// startup-fallback cache in case the backend is unreachable on next
// launch (e.g. user opens the page during Python boot).
//
// CR-052: now async so callers can `await saveTemplates(...)` before
// triggering `loadRows()`.  Previously the IPC save was fire-and-forget
// (`.catch(...)`), which meant `loadRows()` could re-read the backend
// BEFORE the save landed — racing the just-saved list out of the UI
// and re-rendering the pre-save state.  Awaiting guarantees the load
// sees the new state.
async function saveTemplates(
	items: Template[],
	callFn?: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<void> {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
	} catch {
		// localStorage may be unavailable (private mode, quota exceeded).
		// The backend is the source of truth now, so this is non-fatal.
	}
	if (callFn) {
		try {
			await callFn("save_templates", { templates: items });
		} catch (err: unknown) {
			console.error("IPC save_templates failed:", err);
		}
	}
}

/**
 * Generate a stable UUID for a row.  Uses the Web Crypto API
 * (`crypto.randomUUID`) which is available in Electron's renderer
 * (Chromium) and in jsdom (Node ≥ 19).  Falls back to a
 * `Math.random`-based pseudo-ID if `crypto.randomUUID` is unavailable
 * (older runtimes / sandboxed tests) so the React key is still unique
 * within the session — UUID quality doesn't matter here because the
 * ID is never persisted, only used as a React key.
 */
function makeRowId(): string {
	try {
		if (
			typeof crypto !== "undefined" &&
			typeof crypto.randomUUID === "function"
		) {
			return crypto.randomUUID();
		}
	} catch {
		// crypto may be undefined in some test environments
	}
	return `row-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

function toRows(items: Template[]): TemplateRow[] {
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
function rowsToTemplates(rows: TemplateRow[]): Template[] {
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
type TemplateSortOrder = "newest" | "oldest" | "az" | "za";

function sortTemplateRows(
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
function parseImportedTemplates(text: string): Template[] {
	const parsed = JSON.parse(text) as unknown;
	const arr = Array.isArray(parsed)
		? parsed
		: (parsed as { templates?: unknown })?.templates;
	if (!Array.isArray(arr)) {
		throw new Error("File does not contain a templates array");
	}
	return arr.map((t: Partial<Template>) => ({
		trigger: _sanitizeTemplateField(t.trigger),
		output: _sanitizeTemplateField(t.output),
		match_mode: t.match_mode === "contains" ? "contains" : "exact",
	}));
}

export default function TemplatesPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const [templates, setTemplates] = useState<TemplateRow[]>([]);
	const [loading, setLoading] = useState(true);
	// NF-R10-8: surface backend-load failures (IPC error or malformed
	// payload) to the user instead of silently falling back to an
	// empty list. Distinguishes "no templates exist" (valid empty
	// array from backend) from "load failed" (backend unreachable or
	// returned garbage).
	const [loadError, setLoadError] = useState<string | null>(null);
	const [showDialog, setShowDialog] = useState(false);
	const [editingTemplate, setEditingTemplate] = useState<TemplateRow | null>(
		null,
	);
	const [trigger, setTrigger] = useState("");
	const [expansion, setExpansion] = useState("");
	const [matchMode, setMatchMode] = useState<"exact" | "contains">("exact");
	// Search + sort state — applied client-side to the loaded list so the
	// user can re-filter / re-order without an extra backend round-trip.
	const [searchQuery, setSearchQuery] = useState("");
	const [sortOrder, setSortOrder] = useState<TemplateSortOrder>("newest");
	// Hidden file-input ref for the Import button.  We use a hidden
	// ``<input type="file">`` element so the Import button can trigger
	// the OS-native file picker without a custom preload IPC channel.
	const importInputRef = useRef<HTMLInputElement | null>(null);

	// CR-052: ref mirror of `templates` (the React-state TemplateRow[])
	// so `saveTemplate` and the `instantDeleteTemplate` undo callback
	// can read the LATEST list at undo time (potentially seconds after
	// the delete, during which the user may have added/edited/deleted
	// OTHER templates).  Previously both call sites re-read from
	// `loadTemplatesFromLocalStorage()`, which:
	//   1. Could disagree with React state if a `saveTemplates()` call
	//      was still in flight (the old `saveTemplates` was fire-and-
	//      forget on the IPC leg).
	//   2. Used the `tmpl.index` captured at delete time against the
	//      fresh localStorage list — if other operations had shifted
	//      indices in the interim, the splice landed at the WRONG
	//      position (data loss / silent reordering).
	// The ref is kept in sync by the effect below; reads inside
	// callbacks always see the latest committed state.  Mirrors the
	// D2-FIX pattern from Vocabulary.tsx (entriesRef).
	const templatesRef = useRef<TemplateRow[]>(templates);
	useEffect(() => {
		templatesRef.current = templates;
	}, [templates]);

	// NEW-UX-008: load from the Python backend (the new source of truth).
	// On first run after upgrade, if the backend has no templates but
	// localStorage does, push the localStorage data to the backend so the
	// user doesn't lose their pre-existing templates.
	//
	// NF-R10-8: distinguish "no templates exist" (valid empty array
	// from backend) from "load failed" (backend unreachable or
	// returned malformed data). If the backend IPC fails AND the
	// localStorage fallback is also empty, surface a load error so
	// the user can retry instead of being presented with the
	// "create your first template" empty state.
	const loadRows = useCallback(async () => {
		setLoading(true);
		// Clear any prior load error before retrying so the EmptyState
		// swaps back to the spinner during the retry attempt.
		setLoadError(null);
		try {
			let backendTemplates: Template[] = [];
			let backendFailed = false;
			try {
				backendTemplates = await loadTemplatesFromBackend(call);
			} catch (err) {
				// Backend not yet ready (e.g. Python still booting).  Fall
				// back to localStorage so the page is still usable; the next
				// save will resync the backend.
				console.warn(
					"get_templates IPC failed, falling back to localStorage",
					err,
				);
				backendFailed = true;
				backendTemplates = loadTemplatesFromLocalStorage();
			}

			// One-time migration: if backend is empty AND localStorage has
			// data AND we haven't migrated yet, push localStorage → backend.
			const migrated = localStorage.getItem(MIGRATION_FLAG_KEY) === "1";
			if (backendTemplates.length === 0 && !migrated && call) {
				const localItems = loadTemplatesFromLocalStorage();
				if (localItems.length > 0) {
					try {
						await call("save_templates", { templates: localItems });
						backendTemplates = localItems;
						console.warn(
							"[Templates] Migrated %d templates from localStorage to backend",
							localItems.length,
						);
					} catch (err) {
						console.error(
							"Failed to migrate localStorage templates to backend",
							err,
						);
					}
				}
				// Mark migration as complete regardless of whether there was
				// anything to migrate — we don't want to retry on every load.
				try {
					localStorage.setItem(MIGRATION_FLAG_KEY, "1");
				} catch {
					// localStorage unavailable — non-fatal; we'll retry next session.
				}
			}

			setTemplates(toRows(backendTemplates));
			// NF-R10-8: if the backend failed AND we couldn't recover
			// from localStorage (or migration), surface a load error
			// so the user knows to retry. Otherwise the empty list
			// would be indistinguishable from "no templates exist".
			if (backendFailed && backendTemplates.length === 0) {
				setLoadError(
					"Failed to load templates from the backend. Check your connection and try again.",
				);
			}
		} catch (err) {
			console.error("Failed to load templates", err);
			setTemplates([]);
			setLoadError(
				err instanceof Error ? err.message : "Failed to load templates",
			);
		} finally {
			setLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadRows();
	}, [loadRows]);

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
			// CR-052: read from the React-state ref mirror (always
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
				showSnack(
					t("templates.updatedTemplate", { name: trigger.trim() }),
					"success",
				);
			} else {
				items.push(next);
				showSnack(
					t("templates.addedTemplate", { name: trigger.trim() }),
					"success",
				);
			}
			// CR-052: await the IPC save BEFORE loadRows() so the
			// reload is guaranteed to see the just-saved state.
			// Previously `saveTemplates` was fire-and-forget on
			// the IPC leg, so `loadRows()` could re-fetch the
			// pre-save list and briefly render stale data.
			await saveTemplates(items, call);
			setShowDialog(false);
			// NEW-UX-008: reload from backend so the UI stays in sync with
			// what actually persisted (the backend may have rejected or
			// normalized entries).
			loadRows();
		} catch (err) {
			console.error("Failed to save template", err);
			showSnack(t("templates.saveFailed"), "error");
		}
	};

	// NEW-UX-004 / R7-F10: instant-delete path (no confirm dialog).
	// Triggered by the trash icon.  The legacy ConfirmDialog flow
	// was unreachable dead code and has been removed; all deletes
	// now go through this instant-delete + Undo toast path, which is
	// faster and recoverable (6-second undo window).
	//
	// CR-052: the delete + undo now read from `templatesRef.current`
	// (the latest committed React state) instead of from
	// `loadTemplatesFromLocalStorage()`.  We capture `originalIndex`
	// BEFORE the delete (when templatesRef still holds the pre-delete
	// array).  At undo time we re-read `templatesRef.current` (which
	// may reflect add/edit/delete operations performed in the 6s
	// undo window), defensively filter out any item matching the
	// removed one (in case it was re-added in the interim), and
	// splice it back at the captured index CLAMPED to the current
	// length.  This guarantees exactly ONE copy is restored,
	// regardless of concurrent edits — mirroring Vocabulary.tsx's
	// D2-FIX pattern.  Previously the undo re-read from localStorage
	// (which could disagree with React state if a save was in
	// flight) and used the un-clamped `tmpl.index`, so concurrent
	// operations could shift indices and land the restore at the
	// wrong position (silent reordering / data loss).
	const instantDeleteTemplate = useCallback(
		async (tmpl: TemplateRow) => {
			try {
				const items = rowsToTemplates(templatesRef.current);
				const originalIndex = tmpl.index;
				const removed = items.splice(tmpl.index, 1)[0];
				// CR-052: await the IPC save so loadRows()
				// below sees the post-delete state.
				await saveTemplates(items, call);
				if (removed) {
					showUndoableToast(
						t("templates.deletedTemplate", { name: tmpl.trigger }),
						async () => {
							try {
								// Re-read the LATEST list (may include
								// concurrent edits made between the delete
								// and the Undo click).
								const latest = rowsToTemplates(templatesRef.current);
								// Defensively filter out any item matching
								// the removed one (in case it was re-added
								// in the interim) so we don't end up with
								// a duplicate after the splice.
								const filtered = latest.filter(
									(existing) =>
										!(
											existing.trigger === removed.trigger &&
											existing.output === removed.output &&
											existing.match_mode === removed.match_mode
										),
								);
								// Clamp the captured index to the current
								// length so a shrunken list doesn't get
								// an out-of-bounds insert.
								const insertAt =
									originalIndex >= 0
										? Math.min(originalIndex, filtered.length)
										: filtered.length;
								filtered.splice(insertAt, 0, removed);
								await saveTemplates(filtered, call);
								loadRows();
							} catch (err) {
								console.error("Failed to restore template", err);
								showSnack(t("templates.saveFailed"), "error");
							}
						},
						{ undoLabel: t("common.undo"), type: "warning", timeoutMs: 6000 },
					);
				} else {
					showSnack(
						t("templates.deletedTemplate", { name: tmpl.trigger }),
						"warning",
					);
				}
				loadRows();
			} catch (err) {
				console.error("Failed to delete template", err);
				showSnack(t("templates.deleteFailed"), "error");
			}
		},
		[call, loadRows, showSnack],
	);

	const handleTriggerChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setTrigger(e.target.value);

	const handleExpansionChange = (e: React.ChangeEvent<HTMLTextAreaElement>) =>
		setExpansion(e.target.value);

	const handleMatchModeChange = (v: string) =>
		setMatchMode(v as "exact" | "contains");

	const handleCloseDialog = () => setShowDialog(false);

	// ── Import / Export ──────────────────────────────────────────────
	//
	// Export: uses the optional ``window_.exportTemplates`` IPC
	// (NEW-PRIV-007 GDPR right-to-export) when available.  Falls back
	// to a no-op toast if the bridge is missing (e.g. running outside
	// Electron) so the button isn't a silent dead control.
	const doExport = useCallback(async () => {
		try {
			const items = rowsToTemplates(templatesRef.current);
			const bridge = window.window_;
			if (!bridge?.exportTemplates) {
				toast.error(t("vocabulary.exportNotAvailable"));
				return;
			}
			const result = await bridge.exportTemplates({ templates: items });
			if (result.success) {
				const path = result.path ?? "";
				const filename = path.split(/[\\/]/).pop() || "untitled";
				toast.success(t("history.exportSaved", { filename }));
			} else {
				toast.error(result.error || t("history.exportFailed"));
			}
		} catch (err) {
			console.error("Templates export failed:", err);
			toast.error(t("history.exportFailed"));
		}
	}, []);

	// Import: hidden ``<input type="file">`` opens the OS-native picker.
	// We read the file via ``File.text()`` (Chromium ≥ 76, Electron
	// renderer), parse it via ``parseImportedTemplates`` (which accepts
	// both bare-array and ``{templates: [...]}`` shapes), then merge
	// with the existing list (de-duplicating by trigger+output to avoid
	// accidental double-imports) and persist via ``saveTemplates``.
	const handleImportFile = useCallback(
		async (file: File | undefined | null) => {
			if (!file) return;
			try {
				const text = await file.text();
				const imported = parseImportedTemplates(text);
				if (imported.length === 0) {
					toast.error(t("templates.importEmpty"));
					return;
				}
				const existing = rowsToTemplates(templatesRef.current);
				// De-duplicate by ``trigger|output|match_mode`` so re-importing
				// the same file doesn't create duplicate rows.
				const key = (tp: Template) =>
					`${tp.trigger}\u0000${tp.output}\u0000${tp.match_mode}`;
				const existingKeys = new Set(existing.map(key));
				const merged = [...existing];
				let added = 0;
				for (const tp of imported) {
					if (!existingKeys.has(key(tp))) {
						merged.push(tp);
						existingKeys.add(key(tp));
						added++;
					}
				}
				await saveTemplates(merged, call);
				await loadRows();
				if (added === 1) {
					toast.success(t("templates.importSuccessSingular"));
				} else {
					toast.success(
						t("templates.importSuccessPlural", { count: String(added) }),
					);
				}
			} catch (err) {
				console.error("Templates import failed:", err);
				toast.error(
					t("templates.importFailed", {
						error: err instanceof Error ? err.message : String(err),
					}),
				);
			} finally {
				// Reset the input so re-selecting the same file fires
				// ``onChange`` again (otherwise the OS picker suppresses
				// the event if the path is unchanged).
				if (importInputRef.current) importInputRef.current.value = "";
			}
		},
		[call, loadRows],
	);

	const handleImportClick = useCallback(() => {
		importInputRef.current?.click();
	}, []);

	// ── Search + Sort (client-side) ─────────────────────────────────
	//
	// Applied via useMemo so the sort/filter only re-runs when the
	// underlying list, search query, or sort order changes — not on
	// every keystroke that re-renders the page.
	const filteredSortedTemplates = useMemo(() => {
		const q = searchQuery.trim().toLowerCase();
		const filtered = q
			? templates.filter(
					(r) =>
						r.trigger.toLowerCase().includes(q) ||
						r.expansion.toLowerCase().includes(q),
				)
			: templates;
		return sortTemplateRows(filtered, sortOrder);
	}, [templates, searchQuery, sortOrder]);

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// NF-R10-8: distinguish "no templates exist" (valid empty array from
	// backend) from "load failed" (backend unreachable or returned
	// malformed data). When the load genuinely failed AND we have no
	// templates to show (including from localStorage fallback), surface
	// a retry EmptyState instead of the "create your first template"
	// empty state — the latter is misleading when the real issue is a
	// backend connectivity problem.
	if (loadError && templates.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("templates.title")}
					description={t("templates.description")}
				/>
				<EmptyState
					icon={AlertCircleIcon}
					title={t("templates.loadFailedTitle")}
					description={loadError}
					actionLabel={t("templates.retry")}
					onAction={() => loadRows()}
				/>
			</div>
		);
	}

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("templates.title")}
					description={t("templates.description")}
				>
					<div className="flex items-center gap-2">
						{/* Import button — hidden file input + visible trigger.
						    The input is rendered once and re-used; its value
						    is reset after each ``onChange`` so re-selecting
						    the same file fires the event again. */}
						<input
							ref={importInputRef}
							type="file"
							accept="application/json,.json"
							className="sr-only"
							onChange={(e) => {
								const file = e.target.files?.[0];
								handleImportFile(file);
							}}
							aria-hidden="true"
							tabIndex={-1}
						/>
						<Button
							variant="outline"
							size="sm"
							onClick={handleImportClick}
							aria-label={t("common.importAria")}
							className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						>
							{/* Import icon omitted — label is sufficient. */}
							{t("common.import")}
						</Button>
						<ExportFormatMenu
							onExport={() => doExport()}
							disabled={templates.length === 0}
						/>
						<Button
							variant="outline"
							size="sm"
							onClick={openAddDialog}
							aria-label={t("templates.addNewAria")}
							// FIX: muted text/icon by default, white on hover —
							// matches the muted style used by outline buttons
							// elsewhere (History action row, Vocabulary add, etc.).
							className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						>
							<HugeiconsIcon
								icon={Add01Icon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{t("templates.addTemplate")}
						</Button>
					</div>
				</PageHeading>

				{/* Search + sort row — mirrors the History/Vocabulary pattern. */}
				{templates.length > 0 && (
					<div className="mt-4 flex items-center gap-2">
						<div className="flex-1">
							<SearchField
								value={searchQuery}
								onChange={setSearchQuery}
								placeholder={t("templates.searchPlaceholder")}
							/>
						</div>
						<Select
							value={sortOrder}
							onValueChange={(v) => setSortOrder(v as TemplateSortOrder)}
						>
							<SelectTrigger
								size="sm"
								aria-label={t("common.sortAria")}
								className="gap-2 h-9 rounded-xl border-border px-3 text-xs text-(--text-muted) hover:text-(--text-primary)"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="newest">{t("common.sortNewest")}</SelectItem>
								<SelectItem value="oldest">{t("common.sortOldest")}</SelectItem>
								<SelectItem value="az">{t("common.sortAZ")}</SelectItem>
								<SelectItem value="za">{t("common.sortZA")}</SelectItem>
							</SelectContent>
						</Select>
					</div>
				)}

				<div className="mt-4">
					{templates.length === 0 ? (
						<EmptyState
							icon={File02Icon}
							title={t("templates.emptyTitle")}
							description={t("templates.emptyDescription")}
							actionLabel={t("templates.createFirst")}
							onAction={openAddDialog}
						/>
					) : filteredSortedTemplates.length === 0 ? (
						<EmptyState
							icon={File02Icon}
							title={t("templates.emptyTitle")}
							description={t("history.noResultsDescription")}
						/>
					) : (
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
							{filteredSortedTemplates.map((row) => {
								const handleEdit = () => openEditDialog(row);
								const handleDelete = () => instantDeleteTemplate(row);
								// Colored match-mode badge: "exact" → neutral/blue,
								// "contains" → amber.  Uses the same color tokens
								// as the History favorites toggle so the palette
								// stays consistent.
								const isContains = row.match_mode === "contains";
								const matchModeLabel = isContains
									? t("templates.matchModeContainsLabel")
									: t("templates.matchModeExactLabel");
								return (
									<div
										key={row.id}
										className="flex items-center gap-3 px-3.5 py-2.5"
									>
										<div className="min-w-0 flex-1">
											<p className="text-sm font-semibold text-(--text-primary)">
												{row.trigger}
											</p>
											<div className="mt-0.5 flex items-center gap-3">
												<p className="max-w-75 truncate text-xs text-(--text-muted)">
													{row.expansion}
												</p>
												<output
													className={
														"text-xs rounded-full px-2 py-0.5 font-medium " +
														(isContains
															? "bg-amber-400/15 text-amber-700 dark:text-amber-400"
															: "bg-accent/15 text-accent")
													}
													aria-label={t("templates.matchModeAria", {
														mode: matchModeLabel,
													})}
												>
													{row.variables}v &middot; {matchModeLabel}
												</output>
												<InfoTooltip
													text={
														row.used_variables.length > 0
															? t("templates.variablesTooltip", {
																	vars: row.used_variables.join(", "),
																})
															: t("templates.noVariablesTooltip")
													}
												/>
											</div>
										</div>
										<div className="flex shrink-0 items-center gap-0.5">
											<Button
												variant="ghost"
												size="icon-xs"
												onClick={handleEdit}
												className="text-(--text-muted) hover:text-(--text-secondary)"
												title={t("templates.editTemplate")}
												aria-label={t("templates.editAria", {
													name: row.trigger,
												})}
											>
												<HugeiconsIcon
													icon={PencilEdit02Icon}
													strokeWidth={2.5}
													className="h-4 w-4"
												/>
											</Button>
											<Button
												variant="ghost"
												size="icon-xs"
												onClick={handleDelete}
												className="text-(--text-muted) hover:text-destructive"
												title={t("templates.deleteTemplate")}
												aria-label={t("templates.deleteAria", {
													name: row.trigger,
												})}
											>
												<HugeiconsIcon
													icon={Delete01Icon}
													strokeWidth={2.5}
													className="h-4 w-4"
												/>
											</Button>
										</div>
									</div>
								);
							})}
						</div>
					)}
				</div>

				{/* Count footer */}
				{templates.length > 0 && (
					<p className="mt-3 text-xs text-(--text-muted) text-center">
						{t(
							templates.length === 1
								? "templates.countSingular"
								: "templates.countPlural",
							{ count: String(templates.length) },
						)}
					</p>
				)}
			</div>

			{/* Add/Edit Dialog — migrated to shared Modal (F-3) */}
			<Modal
				open={showDialog}
				onClose={handleCloseDialog}
				title={
					editingTemplate ? t("templates.editTitle") : t("templates.addTitle")
				}
				className="w-105"
			>
				<div className="space-y-4">
					<div>
						<label
							htmlFor="template-trigger"
							className="mb-1.5 block text-sm font-medium text-(--text-primary)"
						>
							{t("templates.triggerPhrase")}
						</label>
						<Input
							id="template-trigger"
							value={trigger}
							onChange={handleTriggerChange}
							placeholder={t("templates.triggerPlaceholder")}
							className="w-full"
							// autoFocus removed — Radix Dialog handles first-focus automatically
						/>
						<p className="mt-1.5 text-xs text-(--text-muted)">
							{t("templates.triggerHelp")}
						</p>
					</div>

					<div>
						<label
							htmlFor="template-output"
							className="mb-1.5 block text-sm font-medium text-(--text-primary)"
						>
							{t("templates.outputText")}
						</label>
						<textarea
							id="template-output"
							value={expansion}
							onChange={handleExpansionChange}
							placeholder={t("templates.outputPlaceholder")}
							rows={5}
							className={cn(
								"w-full resize-y rounded-lg border border-border",
								"bg-transparent px-3 py-2 text-sm text-(--text-primary)",
								"placeholder:text-(--text-muted)",
								"focus:border-accent focus:outline-none",
							)}
						/>
						<p className="mt-1.5 text-xs text-(--text-muted)">
							{t("templates.outputHelp")}
							<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{today}`}</code>
							<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{now}`}</code>
							<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{clipboard}`}</code>
							<code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{username}`}</code>
						</p>
					</div>

					<div>
						<span className="mb-1.5 block text-sm font-medium text-(--text-primary)">
							{t("templates.matchMode")}
						</span>
						<Select value={matchMode} onValueChange={handleMatchModeChange}>
							<SelectTrigger
								className="w-full"
								aria-label={t("templates.matchMode")}
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="exact">
									{t("templates.exactMatch")}
								</SelectItem>
								<SelectItem value="contains">
									{t("templates.contains")}
								</SelectItem>
							</SelectContent>
						</Select>
					</div>
				</div>

				<ModalFooter>
					<Button variant="ghost" onClick={handleCloseDialog}>
						{t("common.cancel")}
					</Button>
					<Button variant="default" onClick={saveTemplate}>
						{t("common.save")}
					</Button>
				</ModalFooter>
			</Modal>
		</>
	);
}
