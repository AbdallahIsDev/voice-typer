import {
	Add01Icon,
	AlertCircleIcon,
	BookOpen02Icon,
	Delete01Icon,
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
import type { VocabularyData, VocabularyEntry } from "@/types/ipc";

// ── Backend categories (kept internally for save-back, hidden from UI) ──

const CATEGORIES = [
	"misspellings",
	"phrase_corrections",
	"extra_word_patterns",
	"technical_terms",
	"names",
	"products",
] as const;

// CR-37: getCategoryLabels() — called at render time so locale
// switches re-resolve the t() keys against the new locale.
function getCategoryLabels(): Record<
	string,
	{ label: string; description: string; example: string }
> {
	return {
		misspellings: {
			label: t("vocabulary.category.misspellings"),
			description: t("vocabulary.category.misspellingsDesc"),
			example: "recieve \u2192 receive",
		},
		phrase_corrections: {
			label: t("vocabulary.category.phraseCorrections"),
			description: t("vocabulary.category.phraseCorrectionsDesc"),
			example: "i am going to \u2192 I'm going to",
		},
		extra_word_patterns: {
			label: t("vocabulary.category.extraWordPatterns"),
			description: t("vocabulary.category.extraWordPatternsDesc"),
			example: "um, uh, like \u2192 (removed)",
		},
		technical_terms: {
			label: t("vocabulary.category.technicalTerms"),
			description: t("vocabulary.category.technicalTermsDesc"),
			example: "kubernetes \u2192 Kubernetes",
		},
		names: {
			label: t("vocabulary.category.names"),
			description: t("vocabulary.category.namesDesc"),
			example: "jon \u2192 John",
		},
		products: {
			label: t("vocabulary.category.products"),
			description: t("vocabulary.category.productsDesc"),
			example: "ipad \u2192 iPad",
		},
	};
}

/** Flatten category-shaped VocabularyData into a flat array. */
function flattenEntries(data: VocabularyData): VocabularyEntry[] {
	const items: VocabularyEntry[] = [];
	for (const cat of CATEGORIES) {
		const catData = (data as Record<string, unknown>)[cat];
		if (
			cat === "misspellings" ||
			cat === "technical_terms" ||
			cat === "names" ||
			cat === "products"
		) {
			if (typeof catData === "object" && catData !== null) {
				for (const [key, val] of Object.entries(
					catData as Record<string, string>,
				)) {
					items.push({ category: cat, original: key, correction: String(val) });
				}
			}
		} else if (cat === "phrase_corrections" || cat === "extra_word_patterns") {
			if (Array.isArray(catData)) {
				for (const entry of catData) {
					if (Array.isArray(entry) && entry.length >= 2) {
						items.push({
							category: cat,
							original: entry[0] as string,
							correction: entry[1] as string,
						});
					}
				}
			}
		}
	}
	return items;
}

/** Auto-detect category: phrases (spaces) go to phrase_corrections, single words to misspellings. */
function detectCategory(
	trigger: string,
): "misspellings" | "phrase_corrections" {
	return trigger.includes(" ") ? "phrase_corrections" : "misspellings";
}

/** Rebuild category-shaped VocabularyData from a flat array for server save. */
function rebuildData(entries: VocabularyEntry[]): VocabularyData {
	const data: VocabularyData = {};
	for (const cat of CATEGORIES) {
		const filtered = entries.filter((e) => e.category === cat);
		if (
			cat === "misspellings" ||
			cat === "technical_terms" ||
			cat === "names" ||
			cat === "products"
		) {
			const dict: Record<string, string> = {};
			for (const e of filtered) {
				dict[e.original] = e.correction;
			}
			data[cat] = dict;
		} else {
			data[cat] = filtered.map(
				(e) => [e.original, e.correction] as [string, string],
			);
		}
	}
	return data;
}

/**
 * Sort vocabulary entries client-side.  Mirrors the History/Templates
 * pattern — the backend returns entries in category-bucket order, so
 * "newest"/"oldest" are identity / reverse of the loaded array (the
 * backend doesn't expose per-entry timestamps, so we approximate
 * "newest" as "last in the flattened array" = most recently added
 * under the existing add-to-end semantics).
 *
 * Generic over ``T`` so callers passing ``VocabRow`` (VocabularyEntry
 * + ``_id``) get back ``VocabRow[]`` — preserving the stable UUID
 * through the sort so it can be used as the React key.
 *
 * Uses ``getLocale()`` for the A→Z / Z→A collation so accented
 * characters sort correctly in non-English locales.
 */
type VocabSortOrder = "newest" | "oldest" | "az" | "za";

function sortEntries<T extends VocabularyEntry>(
	items: T[],
	order: VocabSortOrder,
): T[] {
	const locale = getLocale();
	const collator = new Intl.Collator(locale, {
		sensitivity: "base",
		numeric: true,
	});
	const sorted = [...items];
	switch (order) {
		case "oldest":
			// Flattened backend order = oldest first; identity.
			break;
		case "az":
			sorted.sort((a, b) =>
				collator.compare(a.original ?? "", b.original ?? ""),
			);
			break;
		case "za":
			sorted.sort((a, b) =>
				collator.compare(b.original ?? "", a.original ?? ""),
			);
			break;
		default:
			// Reverse so the most-recently-added entry appears at the top.
			sorted.reverse();
			break;
	}
	return sorted;
}

/**
 * Generate a stable UUID for a vocabulary row.  Used as the React key
 * instead of ``${original}-${category}`` (the previous key) which
 * collided when the user added the same trigger word to two different
 * categories, or broke (re-rendered the wrong row) when an edit
 * changed the original text.  Falls back to a ``Math.random``-based
 * pseudo-ID when ``crypto.randomUUID`` is unavailable (sandboxed
 * tests).
 */
function makeEntryId(): string {
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
	return `entry-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

/**
 * Attach a stable client-side UUID to each entry.  The UUID is not
 * persisted — it's regenerated on every load and used only as a React
 * key so list re-orders (sort, filter, undo-restore) don't reuse DOM
 * nodes across different entries.
 */
function withEntryIds(
	entries: VocabularyEntry[],
): (VocabularyEntry & { _id: string })[] {
	return entries.map((e) => ({ ...e, _id: makeEntryId() }));
}

/**
 * Parse an imported vocabulary file.  Accepts:
 *  - A bare JSON array of ``{original, correction, category?}`` objects
 *    (the new export shape — see ``doExport``).
 *  - A backend-shape ``VocabularyData`` object (the legacy / sync
 *    export shape) — flattened via ``flattenEntries``.
 *
 * Throws on malformed JSON or unknown shape so the caller can surface
 * a toast.error with the parse failure reason.
 */
function parseImportedVocabulary(text: string): VocabularyEntry[] {
	const parsed = JSON.parse(text) as unknown;
	if (Array.isArray(parsed)) {
		return parsed
			.filter(
				(
					e: unknown,
				): e is {
					original: unknown;
					correction: unknown;
					category?: unknown;
				} => typeof e === "object" && e !== null,
			)
			.map((e) => ({
				original: typeof e.original === "string" ? e.original : "",
				correction: typeof e.correction === "string" ? e.correction : "",
				category:
					typeof e.category === "string" &&
					CATEGORIES.includes(e.category as (typeof CATEGORIES)[number])
						? e.category
						: detectCategory(typeof e.original === "string" ? e.original : ""),
			}));
	}
	if (parsed && typeof parsed === "object") {
		// Backend-shape VocabularyData — flatten it.
		return flattenEntries(parsed as VocabularyData);
	}
	throw new Error("File does not contain a vocabulary array or data object");
}

// ── Component ──────────────────────────────────────────────────────

export default function VocabularyPage() {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	// VocabRow extends VocabularyEntry with a stable client-side UUID
	// (``_id``) used as the React key.  The UUID is not persisted — it's
	// regenerated on every load — so list re-orders (sort, filter,
	// undo-restore) don't reuse DOM nodes across different entries.
	type VocabRow = VocabularyEntry & { _id: string };
	const [entries, setEntries] = useState<VocabRow[]>([]);

	// CR-37: resolve category labels at render time.
	const categoryLabels = getCategoryLabels();

	// D2-FIX (b-review Finding 4): ref mirror of `entries` so the
	// `instantDeleteEntry` undo callback can read the LATEST list at
	// undo time (potentially seconds after the delete).  Previously the
	// undo callback closed over `entries` from the render that created
	// `instantDeleteEntry` — that snapshot STILL INCLUDED the deleted
	// entry (because `instantDeleteEntry` reads `entries` to compute
	// `updated` via `.filter`, but never replaces `entries` in the
	// closure).  When the user clicked Undo, `restored = [...entries]`
	// contained `entry` at its original index, `restored.indexOf(entry)`
	// returned that index, and `restored.splice(idx, 0, entry)`
	// (deleteCount=0) INSERTED A SECOND COPY at that index — the entry
	// reappeared TWICE after Undo.  The closure was also stale with
	// respect to any other vocabulary edits made between the delete and
	// the Undo click — those edits were silently lost.
	//
	// Mirrors the pattern in Templates.tsx:383, which re-reads via
	// `loadTemplatesFromLocalStorage()` inside the undo callback instead
	// of closing over a stale snapshot.  We use a ref instead of a
	// storage re-read because Vocabulary keeps its source of truth in
	// React state (not localStorage), so a ref is the equivalent.
	const entriesRef = useRef<VocabRow[]>(entries);
	useEffect(() => {
		entriesRef.current = entries;
	}, [entries]);

	const [searchQuery, setSearchQuery] = useState("");
	const [loading, setLoading] = useState(true);
	// NF-R10-1 / fix #8: surface backend-load failures to the user
	// instead of silently masking them as "no entries exist".  Matches
	// the History/Templates retry pattern.
	const [loadError, setLoadError] = useState<string | null>(null);
	const [saving, setSaving] = useState(false);
	const [showDialog, setShowDialog] = useState(false);
	const [editingEntry, setEditingEntry] = useState<VocabRow | null>(null);
	const [trigger, setTrigger] = useState("");
	const [replacement, setReplacement] = useState("");
	// NEW-UX-039: explicit category selection.
	const [category, setCategory] = useState<string>("auto");
	// Sort + category-filter state — applied client-side to the loaded
	// list so the user can re-filter / re-order without an extra backend
	// round-trip.
	const [sortOrder, setSortOrder] = useState<VocabSortOrder>("newest");
	const [categoryFilter, setCategoryFilter] = useState<string>("all");
	// Hidden file-input ref for the Import button (mirrors Templates).
	const importInputRef = useRef<HTMLInputElement | null>(null);

	const handleSearchChange = (value: string) => setSearchQuery(value);

	const handleTriggerChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setTrigger(e.target.value);

	const handleReplacementChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setReplacement(e.target.value);

	const handleCloseDialog = () => setShowDialog(false);

	const doExport = useCallback(
		async (format: "json" | "csv") => {
			try {
				const data = await call<VocabularyData>("get_vocabulary");
				// Include ``category`` in the export payload so re-importing
				// (or importing on another machine) preserves the user's
				// category assignments.  Previously the export stripped
				// category, which meant an imported entry lost its
				// category and fell back to auto-detect — silently
				// undoing the user's manual categorisation.
				const flatData = flattenEntries(data ?? {}).map((e) => ({
					original: e.original,
					correction: e.correction,
					category: e.category,
				}));
				const bridge = window.window_;
				if (!bridge) {
					toast.error(t("vocabulary.exportNotAvailable"));
					return;
				}
				const result = await bridge.exportVocabulary(
					{ entries: flatData },
					format,
				);
				if (result.success) {
					const path = result.path ?? "";
					const filename = path.split(/[\\/]/).pop() || "untitled";
					toast.success(t("vocabulary.exportSaved", { filename }));
				}
			} catch (err) {
				console.error("Vocabulary export failed:", err);
				toast.error(t("vocabulary.exportFailed"));
			}
		},
		[call],
	);

	const loadVocabulary = useCallback(async () => {
		setLoading(true);
		// Clear any prior load error before retrying so the EmptyState
		// swaps back to the spinner during the retry attempt (matches
		// the History/Templates retry pattern).
		setLoadError(null);
		try {
			const data = await call<VocabularyData>("get_vocabulary");
			setEntries(withEntryIds(flattenEntries(data ?? {})));
		} catch (err) {
			console.error("Failed to load vocabulary:", err);
			setEntries([]);
			// NF-R10-1 / fix #8: capture the error message so the render
			// path can show a retry EmptyState instead of an ambiguous
			// empty list.
			setLoadError(
				err instanceof Error ? err.message : "Failed to load vocabulary",
			);
		} finally {
			setLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadVocabulary();
	}, [loadVocabulary]);

	const persistVocabulary = useCallback(
		async (updated: VocabRow[]) => {
			// Strip the client-side ``_id`` before sending to the backend
			// (the backend's save_vocabulary expects the raw
			// VocabularyEntry shape — extra fields would be ignored but
			// we keep the contract clean).
			const stripped: VocabularyEntry[] = updated.map(
				({ _id: _ignored, ...rest }) => {
					void _ignored;
					return rest;
				},
			);
			const data = rebuildData(stripped);
			setSaving(true);
			try {
				await call(
					"save_vocabulary",
					data as unknown as Record<string, unknown>,
				);
			} catch (err) {
				console.error("Failed to save vocabulary:", err);
				throw err;
			} finally {
				setSaving(false);
			}
		},
		[call],
	);

	// Import: hidden ``<input type="file">`` opens the OS-native picker.
	// Mirrors the Templates import pattern.  We parse the file via
	// ``parseImportedVocabulary`` (which accepts both bare-array and
	// backend-shape VocabularyData), de-duplicate by
	// ``original|correction|category`` to avoid double-imports, persist
	// via ``persistVocabulary``, then reload to pick up the merged list.
	const handleImportFile = useCallback(
		async (file: File | undefined | null) => {
			if (!file) return;
			try {
				const text = await file.text();
				const imported = parseImportedVocabulary(text);
				if (imported.length === 0) {
					toast.error(t("vocabulary.importEmpty"));
					return;
				}
				const existing = entriesRef.current.map(
					({ _id: _ignored, ...rest }) => {
						void _ignored;
						return rest;
					},
				);
				const key = (e: VocabularyEntry) =>
					`${e.original}\u0000${e.correction}\u0000${e.category}`;
				const existingKeys = new Set(existing.map(key));
				const merged = [...existing];
				let added = 0;
				for (const e of imported) {
					if (!existingKeys.has(key(e))) {
						merged.push(e);
						existingKeys.add(key(e));
						added++;
					}
				}
				// Attach UUIDs to the merged list before persisting + setState.
				const mergedWithIds: VocabRow[] = withEntryIds(merged);
				await persistVocabulary(mergedWithIds);
				setEntries(mergedWithIds);
				if (added === 1) {
					toast.success(t("vocabulary.importSuccessSingular"));
				} else {
					toast.success(
						t("vocabulary.importSuccessPlural", { count: String(added) }),
					);
				}
			} catch (err) {
				console.error("Vocabulary import failed:", err);
				toast.error(
					t("vocabulary.importFailed", {
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
		[persistVocabulary],
	);

	const handleImportClick = useCallback(() => {
		importInputRef.current?.click();
	}, []);

	// ── Search + Filter + Sort (client-side) ──────────────────────────
	//
	// Applied via useMemo so the filter/sort only re-runs when the
	// underlying list, search query, category filter, or sort order
	// changes — not on every keystroke that re-renders the page.

	const filteredSorted = useMemo(() => {
		const q = searchQuery.trim().toLowerCase();
		const bySearch = q
			? entries.filter(
					(e) =>
						e.original.toLowerCase().includes(q) ||
						e.correction.toLowerCase().includes(q),
				)
			: entries;
		const byCategory =
			categoryFilter === "all"
				? bySearch
				: bySearch.filter((e) => e.category === categoryFilter);
		return sortEntries(byCategory, sortOrder);
	}, [entries, searchQuery, categoryFilter, sortOrder]);

	// `filtered` (search-only, no category filter, no sort) is kept for
	// the existing count-footer and empty-state checks that ask "are
	// there ANY matches for this search?" without the category filter
	// narrowing the count.
	const _filtered = searchQuery.trim()
		? entries.filter(
				(e) =>
					e.original.toLowerCase().includes(searchQuery.toLowerCase()) ||
					e.correction.toLowerCase().includes(searchQuery.toLowerCase()),
			)
		: entries;

	// ── Add / Edit dialog ─────────────────────────────────────────────

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
		// NEW-UX-039: pre-select the entry's existing category.
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
		// NEW-UX-039: use the explicit category if the user picked one.
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

	// NEW-UX-004: instant-delete + Undo toast.  Triggered by the trash
	// icon.  Removes the entry immediately and offers a 6-second Undo
	// window during which the user can restore it.
	//
	// D2-FIX (b-review Finding 4): the undo callback now reads the LATEST
	// `entries` via `entriesRef.current` (kept in sync by the effect
	// declared near the state) instead of closing over the render-time
	// `entries` snapshot.  This fixes two bugs:
	//   1. The stale-closure bug: `[...entries]` previously still
	//      contained the deleted entry, so `indexOf(entry)` returned the
	//      original index and `splice(idx, 0, entry)` (deleteCount=0)
	//      INSERTED a second copy at that index — the entry reappeared
	//      TWICE after Undo.
	//   2. The lost-edits bug: any add/edit of OTHER entries between the
	//      delete and the Undo click were silently reverted because the
	//      restore replaced the current list with the stale pre-delete
	//      snapshot.
	//
	// We capture `originalIndex` BEFORE the delete (when entriesRef still
	// holds the pre-delete array).  At undo time we filter the latest
	// list defensively (in case the entry was somehow re-added in the
	// interim) and splice the entry back at the captured index, clamped
	// to the current length so a shrunken list doesn't get an out-of-
	// bounds insert.  The filter-then-splice combo guarantees exactly
	// ONE copy of the entry is restored, regardless of any concurrent
	// edits.
	//
	// Deps no longer include `entries` — the callback reads from the ref,
	// so its identity is now stable across renders (it only changes when
	// `persistVocabulary` or `showSnack` change, which themselves only
	// change when `call` changes).  This matches the Templates.tsx
	// `instantDeleteTemplate` pattern (deps: [call, loadRows, showSnack]).
	const instantDeleteEntry = useCallback(
		async (entry: VocabRow) => {
			try {
				const currentEntries = entriesRef.current;
				const originalIndex = currentEntries.indexOf(entry);
				const updated = currentEntries.filter((e) => e !== entry);
				await persistVocabulary(updated);
				setEntries(updated);
				showUndoableToast(
					t("vocabulary.deletedEntry", { name: entry.original }),
					async () => {
						try {
							const latest = entriesRef.current.filter((e) => e !== entry);
							const restored = [...latest];
							const insertAt =
								originalIndex >= 0
									? Math.min(originalIndex, restored.length)
									: restored.length;
							restored.splice(insertAt, 0, entry);
							await persistVocabulary(restored);
							setEntries(restored);
							toast.success(t("vocabulary.entryRestored"));
						} catch {
							toast.error(t("vocabulary.restoreFailed"));
						}
					},
					{ undoLabel: t("common.undo"), type: "warning", timeoutMs: 6000 },
				);
			} catch {
				showSnack(t("vocabulary.deleteFailed"), "error");
			}
		},
		[persistVocabulary, showSnack],
	);

	// ── Render ────────────────────────────────────────────────────────

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// NF-R10-1 / fix #8: distinguish "backend failed to load" from
	// "vocabulary is genuinely empty" so the user knows to retry
	// instead of being presented with the add-first-word empty state.
	// Matches the History/Templates retry pattern.
	if (loadError && entries.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("vocabulary.title")}
					description={t("vocabulary.description")}
				/>
				<EmptyState
					icon={AlertCircleIcon}
					title={t("vocabulary.loadFailedTitle")}
					description={loadError}
					actionLabel={t("vocabulary.retry")}
					onAction={() => loadVocabulary()}
				/>
			</div>
		);
	}

	return (
		<>
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("vocabulary.title")}
					description={t("vocabulary.description")}
				>
					<div className="flex items-center gap-2">
						{/* Hidden file input for the Import button (mirrors
						    the Templates pattern). */}
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
							onExport={doExport}
							disabled={entries.length === 0}
						/>
						<Button
							variant="outline"
							size="sm"
							onClick={openAddDialog}
							disabled={saving}
							// FIX: muted text/icon by default, white on hover —
							// matches the sibling Export button (also fixed in
							// ExportFormatMenu) and the outline-button style
							// used across other pages.
							className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
						>
							<HugeiconsIcon
								icon={Add01Icon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
							{t("vocabulary.addWord")}
						</Button>
					</div>
				</PageHeading>

				{/* Search + category filter + sort — mirrors the
				    Templates/History pattern.  Only shown when there
				    are entries to filter/sort (otherwise the empty-state
				    CTA is the only meaningful action). */}
				{entries.length > 0 && (
					<div className="mt-4 flex items-center gap-2">
						<div className="flex-1">
							<SearchField
								value={searchQuery}
								onChange={handleSearchChange}
								placeholder={t("vocabulary.searchPlaceholder")}
							/>
						</div>
						<Select value={categoryFilter} onValueChange={setCategoryFilter}>
							<SelectTrigger
								size="sm"
								aria-label={t("vocabulary.filterByCategoryAria")}
								className="gap-2 h-9 rounded-xl border-border px-3 text-xs text-(--text-muted) hover:text-(--text-primary)"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="all">
									{t("vocabulary.allCategories")}
								</SelectItem>
								{CATEGORIES.map((cat) => (
									<SelectItem key={cat} value={cat}>
										{categoryLabels[cat]?.label ?? cat}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<Select
							value={sortOrder}
							onValueChange={(v) => setSortOrder(v as VocabSortOrder)}
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

				{/* List */}
				<div className="mt-4">
					{entries.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.emptyTitle")}
							description={t("vocabulary.emptyDescription")}
							actionLabel={t("vocabulary.addFirstWord")}
							onAction={openAddDialog}
						/>
					) : filteredSorted.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.noResults")}
						/>
					) : (
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
							{filteredSorted.map((entry) => {
								// Category badge color: each backend category
								// gets a distinct accent so the user can scan
								// the list by category at a glance.  Palette
								// matches the Templates match-mode badge and
								// the History favorites toggle so the visual
								// language stays consistent.
								const catLabel =
									categoryLabels[entry.category]?.label ?? entry.category;
								const catBadgeColor =
									entry.category === "misspellings"
										? "bg-rose-400/15 text-rose-700 dark:text-rose-400"
										: entry.category === "phrase_corrections"
											? "bg-amber-400/15 text-amber-700 dark:text-amber-400"
											: entry.category === "extra_word_patterns"
												? "bg-slate-400/15 text-slate-700 dark:text-slate-300"
												: entry.category === "technical_terms"
													? "bg-sky-400/15 text-sky-700 dark:text-sky-400"
													: entry.category === "names"
														? "bg-violet-400/15 text-violet-700 dark:text-violet-400"
														: "bg-emerald-400/15 text-emerald-700 dark:text-emerald-400";
								return (
									<div
										key={entry._id}
										className="flex items-start gap-3 px-3.5 py-2.5"
									>
										<div className="min-w-0 flex-1">
											<div className="flex items-center gap-2.5">
												<span className="text-sm dark:font-normal font-medium text-destructive tracking-wider">
													{entry.original}{" "}
												</span>
												<span className="text-sm text-(--text-muted)">→</span>
												<span className="text-sm font-semibold text-(--text-primary)">
													{entry.correction}{" "}
												</span>
												{/* Category badge — surfaces the backend
												    category so the user can see at a
												    glance which bucket each entry belongs
												    to (previously the category was
												    hidden in the dialog only). */}{" "}
												<output
													className={
														"text-[10px] rounded-full px-2 py-0.5 font-medium uppercase tracking-wide " +
														catBadgeColor
													}
													aria-label={t("vocabulary.categoryBadgeAria", {
														category: catLabel,
													})}
												>
													{catLabel}{" "}
												</output>
											</div>
										</div>
										<div className="flex shrink-0 items-center gap-1">
											<Button
												variant="ghost"
												size="icon-xs"
												onClick={() => openEditDialog(entry)}
												className="text-(--text-muted) hover:text-accent"
												title={t("vocabulary.edit")}
												aria-label={t("vocabulary.editAria", {
													name: entry.original,
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
												onClick={() => instantDeleteEntry(entry)}
												className="text-(--text-muted) hover:text-destructive"
												title={t("common.delete")}
												aria-label={t("vocabulary.deleteAria", {
													name: entry.original,
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
				{entries.length > 0 && !searchQuery.trim() && (
					<p className="mt-3 text-xs text-(--text-muted) text-center">
						{t(
							entries.length === 1
								? "vocabulary.entryCountSingular"
								: "vocabulary.entryCountPlural",
							{ count: String(entries.length) },
						)}
					</p>
				)}
			</div>

			{/* Add/Edit Dialog — migrated to shared Modal (F-3) */}
			<Modal
				open={showDialog}
				onClose={handleCloseDialog}
				title={
					editingEntry
						? t("vocabulary.editEntryTitle")
						: t("vocabulary.addEntryTitle")
				}
				className="w-105"
			>
				<div className="space-y-4">
					<div>
						<label
							htmlFor="vocab-trigger"
							className="mb-1.5 block text-sm font-medium text-(--text-primary)"
						>
							{t("vocabulary.whatYouSay")}
						</label>
						<Input
							id="vocab-trigger"
							value={trigger}
							onChange={handleTriggerChange}
							placeholder={t("vocabulary.triggerPlaceholder")}
							className="w-full"
							// autoFocus removed — Radix Dialog handles first-focus automatically (matches the Templates pattern).
						/>
						<p className="mt-1.5 text-xs text-(--text-muted)">
							{t("vocabulary.triggerHelp")}
						</p>
					</div>

					<div>
						<label
							htmlFor="vocab-replacement"
							className="mb-1.5 block text-sm font-medium text-(--text-primary)"
						>
							{t("vocabulary.whatGetsTyped")}
						</label>
						<Input
							id="vocab-replacement"
							value={replacement}
							onChange={handleReplacementChange}
							placeholder={t("vocabulary.replacementPlaceholder")}
							className="w-full"
						/>
						<p className="mt-1.5 text-xs text-(--text-muted)">
							{t("vocabulary.replacementHelp")}
						</p>
					</div>

					{/* NEW-UX-039: explicit category picker. */}
					<div>
						<span className="mb-1.5 block text-sm font-medium text-(--text-primary)">
							{t("vocabulary.categoryLabel")}
						</span>
						<Select value={category} onValueChange={setCategory}>
							<SelectTrigger
								className="w-full"
								aria-label={t("vocabulary.categoryAria")}
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="auto">
									<span className="flex flex-col">
										<span>{t("vocabulary.category.autoDetect")}</span>
										<span className="text-xs text-(--text-muted)">
											{t("vocabulary.category.autoDetectDesc")}
										</span>
									</span>
								</SelectItem>
								{CATEGORIES.map((cat) => (
									<SelectItem key={cat} value={cat}>
										<span className="flex flex-col">
											<span>{categoryLabels[cat]?.label ?? cat}</span>
											<span className="text-xs text-(--text-muted)">
												{categoryLabels[cat]?.example ?? ""}
											</span>
										</span>
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						{category !== "auto" && categoryLabels[category] && (
							<p className="mt-1.5 text-xs text-(--text-muted)">
								{categoryLabels[category].description}
							</p>
						)}
					</div>
				</div>

				<ModalFooter>
					<Button variant="ghost" onClick={handleCloseDialog}>
						{t("common.cancel")}
					</Button>
					<Button
						variant="default"
						onClick={saveEntry}
						disabled={!trigger.trim() || !replacement.trim()}
					>
						{t("common.save")}
					</Button>
				</ModalFooter>
			</Modal>
		</>
	);
}
