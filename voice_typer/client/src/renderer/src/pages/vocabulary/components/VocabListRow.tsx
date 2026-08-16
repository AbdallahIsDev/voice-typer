// One row of the vocabulary list — a clean two-column pairing.
//
// Simplified for scannability:
//   - leading checkbox (bulk selection)
//   - the wrong→correct pairing as two connected text spans with an
//     arrow ("this becomes that")
//   - direct Edit + Delete icon buttons on the right (larger touch
//     target, hover states, tooltips, aria-labels) — no overflow menu
//   - responsive: on narrow widths the corrected half stacks below the
//     original (connector arrow stays visible) instead of overflowing
//
// The row is memoized — the parent passes stable useCallback handlers
// so a search keystroke (which re-renders the page but changes no row
// props) skips every row's render. ``testResult`` is ``null`` for every
// row except the one being tested, so an in-flight test re-renders
// only its own row.
import {
	ArrowRight01Icon,
	Delete01Icon,
	PencilEdit02Icon,
	TestTubeIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
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
	// Grid: [checkbox][original][corrected][actions] on sm+; on narrow
	// widths the corrected half moves to its own line below the
	// original (col 2), keeping the connector arrow visible.
	return (
		<div
			key={entry._id}
			data-testid="vocab-list-row"
			data-selected={selected ? "true" : "false"}
			className={cn(
				"grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 px-3.5 py-2.5 transition-colors hover:bg-foreground/5 sm:grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_auto]",
				selected && "bg-accent/10 hover:bg-accent/10",
			)}
		>
			{/* Checkbox (col 1) — bulk selection. */}
			<label className="flex cursor-pointer items-center self-start pt-0.5 sm:self-center sm:pt-0">
				<input
					type="checkbox"
					checked={selected}
					onChange={() => onToggleSelect(entry._id)}
					aria-label={t("vocabulary.selectEntry", { name: entry.original })}
					className="size-4 shrink-0 cursor-pointer accent-[color-mix(in_oklab,var(--accent)_60%,transparent)] focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
				/>
			</label>

			{/* Original (col 2) — what the recognizer mishears, styled
			    red to signal "incorrect". Below it, the server-tracked
			    usage line ("Used N× · last used …") when the correction
			    has actually fired during dictation. */}
			<div className="flex min-w-0 flex-col items-start gap-0.5">
				<span
					title={entry.original}
					className="min-w-0 truncate text-sm font-medium text-destructive tracking-wider"
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

			{/* Corrected (col 3 on sm+; row 2 on mobile) — arrow + text,
			    styled bold/primary to signal "correct". */}
			<span className="col-start-2 flex min-w-0 items-center gap-1.5 sm:col-start-auto">
				<HugeiconsIcon
					icon={ArrowRight01Icon}
					strokeWidth={2.25}
					aria-hidden="true"
					className="size-3.5 shrink-0 text-(--text-muted)"
				/>
				<span
					title={entry.correction}
					className="min-w-0 truncate text-sm font-semibold text-(--text-primary)"
				>
					{entry.correction}
				</span>
			</span>

			{/* Actions (col 4 on sm+; col 3 on mobile, same row as the
			    checkbox): Test this entry + Edit + Delete. Test is a
			    diagnostic — it runs the wrong phrase through the LIVE
			    server engine and shows the authoritative result inline
			    below the row. */}
			<div className="flex items-center justify-self-end gap-0.5">
				<Tooltip>
					<TooltipTrigger asChild>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("vocabulary.testEntryAria", {
								name: entry.original,
							})}
							title={t("vocabulary.testEntry")}
							onClick={() => onTest(entry)}
							className="text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-accent"
						>
							<HugeiconsIcon
								icon={TestTubeIcon}
								strokeWidth={2.25}
								aria-hidden="true"
								className="size-4"
							/>
						</Button>
					</TooltipTrigger>
					<TooltipContent side="left">
						{t("vocabulary.testEntry")}
					</TooltipContent>
				</Tooltip>
				<Tooltip>
					<TooltipTrigger asChild>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("vocabulary.editAria", { name: entry.original })}
							title={t("vocabulary.edit")}
							onClick={() => onEdit(entry)}
							className="text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-(--text-primary)"
						>
							<HugeiconsIcon
								icon={PencilEdit02Icon}
								strokeWidth={2.25}
								aria-hidden="true"
								className="size-4"
							/>
						</Button>
					</TooltipTrigger>
					<TooltipContent side="left">{t("vocabulary.edit")}</TooltipContent>
				</Tooltip>{" "}
				<Tooltip>
					<TooltipTrigger asChild>
						<Button
							variant="ghost"
							size="icon-sm"
							aria-label={t("vocabulary.deleteAria", { name: entry.original })}
							title={t("common.delete")}
							onClick={() => onDelete(entry)}
							className="text-(--text-muted) transition-colors hover:bg-destructive/10 hover:text-destructive"
						>
							<HugeiconsIcon
								icon={Delete01Icon}
								strokeWidth={2.25}
								aria-hidden="true"
								className="size-4"
							/>
						</Button>
					</TooltipTrigger>
					<TooltipContent side="left">{t("common.delete")}</TooltipContent>
				</Tooltip>
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
					{testResult.status === "running" && (
						<div className="flex items-center gap-2 text-xs text-(--text-muted)">
							<Spinner decorative size={12} className="border-current" />
							{t("vocabulary.testEntryPending")}
						</div>
					)}
					{testResult.status === "done" &&
						(testResult.applied ? (
							<div className="rounded-lg border border-emerald-400/30 bg-emerald-400/10 px-3 py-2">
								<p className="text-[11px] font-semibold uppercase tracking-wider text-(--text-muted)">
									{t("vocabulary.testCorrected")}
								</p>
								<p className="mt-0.5 whitespace-pre-wrap break-words text-sm text-(--text-primary)">
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
								onClick={() => onTest(entry)}
								className="cursor-pointer rounded-full border border-border/10 bg-(--bg-subtle) px-2.5 py-0.5 font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5"
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
