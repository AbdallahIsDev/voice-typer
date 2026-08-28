// One row of the vocabulary list — a clean two-column pairing.
//
// Simplified for scannability:
//   - leading checkbox (bulk selection)
//   - the wrong→correct pairing as two labeled text spans ("Heard as"
//     → "Corrected to" per the column headers; the connector arrow was
//     removed — the columns are clearly labeled and positioned
//     left/right)
//   - direct Edit + Test + Delete icon buttons on the right (larger
//     touch target, hover states, aria-labels) — no overflow menu and
//     NO tooltips (they rendered over the adjacent icons while moving
//     the cursor); Delete is LAST (destructive actions never lead the
//     group)
//   - the WHOLE row toggles selection on click (bulk-select pattern) —
//     action buttons and the checkbox stop propagation so they keep
//     working independently
//   - responsive: on narrow widths the corrected half stacks below the
//     original instead of overflowing
//
// The row is memoized — the parent passes stable useCallback handlers
// so a search keystroke (which re-renders the page but changes no row
// props) skips every row's render. ``testResult`` is ``null`` for every
// row except the one being tested, so an in-flight test re-renders
// only its own row.
import {
	Delete01Icon,
	PencilEdit02Icon,
	TestTube01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useEffect, useState } from "react";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { getLocale, t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

import type { EntryUsage } from "../hooks/useVocabulary";
import type { EntryTestResult } from "../lib/testServer";
import type { VocabRow } from "../lib/transform";

/** Compact date for the "last used" line (e.g. "Aug 14"), locale-aware. */
function formatUsageDate(ts: number): string {
	try {
		return new Intl.DateTimeFormat(getLocale(), {
			month: "short",
			day: "numeric",
		}).format(new Date(ts * 1000));
	} catch {
		return new Date(ts * 1000).toLocaleDateString();
	}
}

interface VocabListRowProps {
	entry: VocabRow;
	selected: boolean;
	onToggleSelect: (id: string) => void;
	onEdit: (entry: VocabRow) => void;
	onDelete: (entry: VocabRow) => void;
	/**
	 * "Test this entry" — runs the entry's wrong phrase through the
	 * LIVE server correction engine (``test_vocabulary_correction``
	 * IPC → ``VocabularyManager.apply_to_text``).
	 */
	onTest: (entry: VocabRow) => void;
	/** Inline result of the live-engine test for THIS row (null when idle). */
	testResult: EntryTestResult | null;
	/**
	 * Server-tracked usage for THIS entry (null/undefined when the
	 * correction never fired or usage data isn't available). Renders a
	 * subtle "Used N× · last used …" line under the wrong phrase.
	 */
	usage?: EntryUsage | null;
}

export const VocabListRow = memo(function VocabListRow({
	entry,
	selected,
	onToggleSelect,
	onEdit,
	onDelete,
	onTest,
	testResult,
	usage,
}: VocabListRowProps) {
	// The "Testing with the live engine…" pending row is only painted
	// after ~300ms of continuous "running": the engine usually answers
	// in a few milliseconds, and painting a spinner that immediately
	// disappears is a jarring flash-in-flash-out. Only genuinely slow
	// responses (cold start / busy backend) ever surface the pending
	// state; fast ones jump straight from nothing to the result.
	const [pendingVisible, setPendingVisible] = useState(false);
	useEffect(() => {
		if (testResult?.status !== "running") {
			setPendingVisible(false);
			return;
		}
		const timer = window.setTimeout(() => setPendingVisible(true), 300);
		return () => window.clearTimeout(timer);
	}, [testResult]);
	// Grid: [checkbox][original][corrected][actions] on sm+; on narrow
	// widths the corrected half moves to its own line below the
	// original (col 2). The sm+ ACTIONS column is FIXED at 6.25rem
	// (100px — the three icon buttons) so it matches the header's fixed
	// actions column: with ``auto`` the header's short "Actions" label
	// would split the 1fr columns differently than the rows' wider icon
	// cluster and the header's "Corrected to" label would sit to the
	// right of the row values (see VocabListHeader for the invariant).
	//
	// The row is clickable as a whole (toggle selection) — that's what
	// the hover background implies. Action buttons and the checkbox
	// stop propagation so they don't double-toggle.
	return (
		// The row click is a mouse-only convenience for bulk
		// selection — keyboard/SR users toggle via the nested
		// Checkbox (a real role="checkbox" button). Making the row
		// itself keyboard-activatable would double-toggle with the
		// Checkbox's own handler.
		// biome-ignore lint/a11y/noStaticElementInteractions: the nested Checkbox is the accessible control.
		// biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation would double-toggle with the nested Checkbox; the Checkbox provides the keyboard path.
		<div
			key={entry._id}
			data-testid="vocab-list-row"
			data-selected={selected ? "true" : "false"}
			onClick={() => onToggleSelect(entry._id)}
			className={cn(
				"grid cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5 transition-colors hover:bg-foreground/5 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_6.25rem]",
				selected && "bg-accent/10 hover:bg-accent/10",
			)}
		>
			{/* Checkbox (col 1) — bulk selection. Its own click already
			    toggles selection; the onClick stops propagation so the
			    row's click-to-toggle handler doesn't double-toggle. (The
			    design-system Checkbox is a <button> — its click never
			    bubbles past this point.) */}
			<Checkbox
				checked={selected}
				onCheckedChange={() => onToggleSelect(entry._id)}
				onClick={(e) => e.stopPropagation()}
				aria-label={t("vocabulary.selectEntry", { name: entry.original })}
				className="self-start pt-0.5 sm:self-center sm:pt-0"
			/>
			{/* Original (col 2) — what the recognizer mishears, styled
			    red to signal "incorrect". Below it, the server-tracked
			    usage line ("Used N× · last used …") when the correction
			    has actually fired during dictation. */}
			<div className="flex min-w-0 flex-col items-start gap-0.5">
				<span
					title={entry.original}
					className="min-w-0 truncate text-sm font-medium text-destructive tracking-wide"
				>
					{entry.original}
				</span>
				{usage && usage.count > 0 && (
					<span
						data-testid="vocab-entry-usage"
						className="truncate text-[11px] text-(--text-muted)"
					>
						{t("vocabulary.usedCount", { count: String(usage.count) })}
						{usage.last_ts > 0 &&
							` · ${t("vocabulary.lastUsed", {
								date: formatUsageDate(usage.last_ts),
							})}`}
					</span>
				)}
			</div>
			{/* Corrected (col 3 on sm+; row 2 on mobile) — bold/primary
			    to signal "correct". */}
			<span className="col-start-2 flex min-w-0 items-center sm:col-start-auto">
				<span
					title={entry.correction}
					className="min-w-0 truncate text-sm font-medium text-(--text-primary)"
				>
					{entry.correction}
				</span>
			</span>
			{/* Actions (col 4 on sm+; col 3 on mobile, same row as the
			    checkbox): Test + Delete + Edit (Edit RIGHTMOST — the
			    app-wide action-icon ordering convention: the edit pencil
			    is always the last icon in the group, on every page that
			    uses this pattern). Test is a diagnostic — it runs the
			    wrong phrase through the LIVE server engine and shows the
			    authoritative result inline below the row. */}{" "}
			<div className="flex items-center justify-self-end gap-0.5">
				{/* Test → Delete → Edit (Edit rightmost, matching the
				    app-wide convention). NO tooltips on any of the three:
				    hover tooltips rendered over the adjacent icons while
				    moving the cursor between them, and the shapes +
				    aria-labels carry the meaning. */}
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label={t("vocabulary.testEntryAria", {
						name: entry.original,
					})}
					onClick={(e) => {
						e.stopPropagation();
						// This row's result is already displayed (or still in
						// flight): clicking the icon again must NOT re-run the
						// engine — that flashes the loading state over a result
						// that's already known. No-op; the Retry button inside
						// the error block is the explicit re-run path.
						if (testResult) return;
						onTest(entry);
					}}
					className="text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-accent"
				>
					<HugeiconsIcon
						icon={TestTube01Icon}
						strokeWidth={2.25}
						aria-hidden="true"
						className="size-4"
					/>
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label={t("vocabulary.deleteAria", { name: entry.original })}
					onClick={(e) => {
						e.stopPropagation();
						onDelete(entry);
					}}
					className="text-(--text-muted) transition-colors hover:bg-destructive/10 hover:text-destructive"
				>
					<HugeiconsIcon
						icon={Delete01Icon}
						strokeWidth={2.25}
						aria-hidden="true"
						className="size-4"
					/>
				</Button>
				<Button
					variant="ghost"
					size="icon-sm"
					aria-label={t("vocabulary.editAria", { name: entry.original })}
					onClick={(e) => {
						e.stopPropagation();
						onEdit(entry);
					}}
					className="text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={PencilEdit02Icon}
						strokeWidth={2.25}
						aria-hidden="true"
						className="size-4"
					/>
				</Button>
			</div>
			{/* Inline live-engine test result — spans the full row width
			    below the pairing. role="status" announces the transition
			    (running → result/error) to screen readers. */}
			{testResult && (
				<div
					data-testid="vocab-entry-test-result"
					role="status"
					className="col-span-full ms-10 -mt-0.5 min-w-0"
				>
					{testResult.status === "running" && pendingVisible && (
						<div className="flex items-center gap-2 text-xs text-(--text-muted)">
							<Spinner decorative size={12} className="border-current" />
							{t("vocabulary.testEntryPending")}
						</div>
					)}
					{testResult.status === "done" &&
						(testResult.applied ? (
							// Same surface treatment as the search input —
							// the "this is correct" signal lives in the
							// corrected text's green colour, not a
							// green-bordered box.
							<div className="rounded-xl border border-border/5 bg-(--bg) px-3 py-2">
								<p className="text-[11px] font-semibold uppercase tracking-wide text-(--text-muted)">
									{t("vocabulary.testCorrected")}
								</p>
								<p className="mt-0.5 whitespace-pre-wrap wrap-break-word text-sm font-medium text-emerald-700 dark:text-emerald-400">
									{testResult.output}
								</p>
							</div>
						) : (
							<p className="text-xs font-medium text-amber-700 dark:text-amber-400">
								{t("vocabulary.testEntryNoChange")}
							</p>
						))}
					{testResult.status === "error" && (
						<div className="flex flex-wrap items-center gap-2 text-xs">
							<span className="text-destructive">
								{t("vocabulary.testEntryFailed")}
							</span>
							<button
								type="button"
								onClick={(e) => {
									e.stopPropagation();
									onTest(entry);
								}}
								className="cursor-pointer rounded-full border border-border/5 bg-(--bg-subtle) px-2.5 py-0.5 font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5"
							>
								{t("vocabulary.retry")}
							</button>
						</div>
					)}
				</div>
			)}
		</div>
	);
});
