import {
	Add01Icon,
	BookOpen02Icon,
	Delete01Icon,
	PencilEdit02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import ExportFormatMenu from "@/components/common/ExportFormatMenu";
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
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
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

// NEW-UX-039: human-readable labels + descriptions for each category.
const CATEGORY_LABELS: Record<
	string,
	{ label: string; description: string; example: string }
> = {
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

// ── Component ──────────────────────────────────────────────────────

export default function VocabularyPage() {
	const { call } = usePython();
	const { showSnack, Snackbar } = useSnackbar();
	const [entries, setEntries] = useState<VocabularyEntry[]>([]);

	const [searchQuery, setSearchQuery] = useState("");
	const [loading, setLoading] = useState(true);
	const [saving, setSaving] = useState(false);
	const [showDialog, setShowDialog] = useState(false);
	const [editingEntry, setEditingEntry] = useState<VocabularyEntry | null>(
		null,
	);
	const [trigger, setTrigger] = useState("");
	const [replacement, setReplacement] = useState("");
	// NEW-UX-039: explicit category selection.
	const [category, setCategory] = useState<string>("auto");

	// #7: ConfirmDialog state for entry deletion
	const [deleteEntryTarget, setDeleteEntryTarget] =
		useState<VocabularyEntry | null>(null);
	const dialogRef = useRef<HTMLDivElement>(null);

	const handleSearchChange = (value: string) => setSearchQuery(value);

	const handleTriggerChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setTrigger(e.target.value);

	const handleReplacementChange = (e: React.ChangeEvent<HTMLInputElement>) =>
		setReplacement(e.target.value);

	const handleCloseDialog = () => setShowDialog(false);

	const handleCancelDelete = () => setDeleteEntryTarget(null);

	const handleBackdropClick = (e: React.MouseEvent) => {
		if (e.target === e.currentTarget) setShowDialog(false);
	};

	const handleDialogKeyDown = useCallback((e: React.KeyboardEvent) => {
		if (e.key === "Escape") {
			setShowDialog(false);
			return;
		}
		if (e.key !== "Tab") return;
		const dialog = dialogRef.current;
		if (!dialog) return;
		const focusable = dialog.querySelectorAll<HTMLElement>(
			'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
		);
		if (focusable.length === 0) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		if (e.shiftKey && document.activeElement === first) {
			e.preventDefault();
			last.focus();
		} else if (!e.shiftKey && document.activeElement === last) {
			e.preventDefault();
			first.focus();
		}
	}, []);

	const doExport = useCallback(
		async (format: "json" | "csv") => {
			try {
				const data = await call<VocabularyData>("get_vocabulary");
				// Flatten and strip internal categories before passing to the bridge
				const flatData = flattenEntries(data ?? {}).map((e) => ({
					original: e.original,
					correction: e.correction,
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
		try {
			const data = await call<VocabularyData>("get_vocabulary");
			setEntries(flattenEntries(data ?? {}));
		} catch (err) {
			console.error("Failed to load vocabulary:", err);
			setEntries([]);
		} finally {
			setLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadVocabulary();
	}, [loadVocabulary]);

	const persistVocabulary = useCallback(
		async (updated: VocabularyEntry[]) => {
			const data = rebuildData(updated);
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

	// ── Search ─────────────────────────────────────────────────────────

	const filtered = searchQuery.trim()
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

	const openEditDialog = (entry: VocabularyEntry) => {
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
			let updated: VocabularyEntry[];
			if (editingEntry) {
				updated = entries.map((e) =>
					e === editingEntry
						? {
								category: resolvedCategory as VocabularyEntry["category"],
								original: trimmedTrigger,
								correction: r,
							}
						: e,
				);
			} else {
				updated = [
					...entries,
					{
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

	// #7: Request confirmation before deleting an entry.
	// NEW-UX-004: This path is kept for accessibility users; the
	// default trash-icon click path now uses instant-delete + Undo
	// toast via ``instantDeleteEntry`` (below).
	const _requestDeleteEntry = (entry: VocabularyEntry) => {
		setDeleteEntryTarget(entry);
	};

	const confirmDeleteEntry = async () => {
		if (!deleteEntryTarget) return;
		try {
			const updated = entries.filter((e) => e !== deleteEntryTarget);
			await persistVocabulary(updated);
			setEntries(updated);
			showSnack(
				t("vocabulary.deletedEntry", { name: deleteEntryTarget.original }),
				"warning",
			);
		} catch {
			showSnack(t("vocabulary.deleteFailed"), "error");
		} finally {
			setDeleteEntryTarget(null);
		}
	};

	// NEW-UX-004: instant-delete + Undo toast.  Triggered by the trash
	// icon.  Removes the entry immediately and offers a 6-second Undo
	// window during which the user can restore it.
	const instantDeleteEntry = useCallback(
		async (entry: VocabularyEntry) => {
			try {
				const updated = entries.filter((e) => e !== entry);
				await persistVocabulary(updated);
				setEntries(updated);
				showUndoableToast(
					t("vocabulary.deletedEntry", { name: entry.original }),
					async () => {
						try {
							const restored = [...entries];
							// Re-insert at the original location (best-effort: append
							// if the reference position is no longer valid).
							const idx = restored.indexOf(entry);
							if (idx >= 0) {
								restored.splice(idx, 0, entry);
							} else {
								restored.push(entry);
							}
							await persistVocabulary(restored);
							setEntries(restored);
							toast.success(t("vocabulary.entryRestored"));
						} catch {
							toast.error(t("vocabulary.restoreFailed"));
						}
					},
					{ undoLabel: "Undo", type: "warning", timeoutMs: 6000 },
				);
			} catch {
				showSnack(t("vocabulary.deleteFailed"), "error");
			}
		},
		[entries, persistVocabulary, showSnack],
	);

	// ── Render ────────────────────────────────────────────────────────

	if (loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
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

				{/* Search */}
				<SearchField
					value={searchQuery}
					onChange={handleSearchChange}
					placeholder={t("vocabulary.searchPlaceholder")}
				/>

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
					) : filtered.length === 0 ? (
						<EmptyState
							icon={BookOpen02Icon}
							title={t("vocabulary.noResults")}
						/>
					) : (
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
							{filtered.map((entry) => (
								<div
									key={`${entry.original}-${entry.category}`}
									className="flex items-start gap-3 px-3.5 py-2.5"
								>
									<div className="min-w-0 flex-1">
										<div className="flex items-center gap-2.5">
											<span className="text-sm dark:font-normal font-medium text-destructive tracking-wider">
												{entry.original}
											</span>
											<span className="text-sm text-(--text-muted)">→</span>
											<span className="text-sm font-semibold text-(--text-primary)">
												{entry.correction}
											</span>
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
							))}
						</div>
					)}
				</div>

				{/* Count footer */}
				{entries.length > 0 && !searchQuery.trim() && (
					<p className="mt-3 text-[10px] text-(--text-muted) text-center opacity-50">
						{t(
							entries.length === 1
								? "vocabulary.entryCountSingular"
								: "vocabulary.entryCountPlural",
							{ count: String(entries.length) },
						)}
					</p>
				)}

				{/* Snackbar */}
				<Snackbar />
			</div>

			{/* Add/Edit Dialog */}
			{showDialog && (
				<div
					ref={dialogRef}
					role="dialog"
					aria-modal="true"
					aria-labelledby="vocabulary-dialog-title"
					className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
					onClick={handleBackdropClick}
					onKeyDown={handleDialogKeyDown}
				>
					<div
						className={cn(
							"animate-scale-in w-105 rounded-xl border border-border",
							"bg-(--bg) p-6",
						)}
					>
						<h2
							id="vocabulary-dialog-title"
							className="mb-5 text-lg font-semibold text-(--text-primary)"
						>
							{editingEntry
								? t("vocabulary.editEntryTitle")
								: t("vocabulary.addEntryTitle")}
						</h2>

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
									placeholder="treat three, mynameis"
									className="w-full"
									autoFocus
								/>
								{/* NEW-UX-026: help text explaining what to type. */}
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
									placeholder="treat this, My Name Is"
									className="w-full"
								/>
								{/* NEW-UX-026: help text for the replacement field. */}
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
													<span>{CATEGORY_LABELS[cat]?.label ?? cat}</span>
													<span className="text-xs text-(--text-muted)">
														{CATEGORY_LABELS[cat]?.example ?? ""}
													</span>
												</span>
											</SelectItem>
										))}
									</SelectContent>
								</Select>
								{category !== "auto" && CATEGORY_LABELS[category] && (
									<p className="mt-1.5 text-xs text-(--text-muted)">
										{CATEGORY_LABELS[category].description}
									</p>
								)}
							</div>
						</div>

						<div className="mt-6 flex justify-end gap-3">
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
						</div>
					</div>
				</div>
			)}

			{/* #7: ConfirmDialog for entry deletion */}
			<ConfirmDialog
				open={deleteEntryTarget !== null}
				title={t("vocabulary.deleteEntryTitle")}
				message={t("vocabulary.deleteEntryMessage", {
					name: deleteEntryTarget?.original ?? "",
				})}
				confirmLabel={t("common.delete")}
				onConfirm={confirmDeleteEntry}
				onCancel={handleCancelDelete}
			/>
		</>
	);
}
