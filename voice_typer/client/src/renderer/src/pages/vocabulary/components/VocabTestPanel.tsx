// Live "Test corrections" panel.
//
// Collapsible section above the list: the user types a phrase and sees
// the corrected output in real time. The output comes from
// ``useVocabularyTester`` (wired in the page), which runs the phrase
// through the LIVE backend correction engine
// (``test_vocabulary_correction`` IPC → ``VocabularyManager.apply_to_text``)
// with a client-side mirror fallback — so the preview uses the exact
// rules dictation applies, not a UI-only approximation.
//
// The applied state is highlighted (green tint) so the user can see
// which parts of the phrase changed at a glance. While the debounced
// round-trip is in flight a "Testing…" indicator is shown.
import { ArrowDown01Icon, TestTubeIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { Input } from "@/components/ui/input";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface VocabTestPanelProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	query: string;
	onQueryChange: (q: string) => void;
	output: string;
	applied: boolean;
	pending: boolean;
	/**
	 * True when the backend round-trip failed and the preview fell
	 * back to the client-side mirror. Surfaced so a preview is never
	 * silently presented as the authoritative engine result.
	 */
	usingFallback: boolean;
}

export function VocabTestPanel({
	open,
	onOpenChange,
	query,
	onQueryChange,
	output,
	applied,
	pending,
	usingFallback,
}: VocabTestPanelProps) {
	const active = query.trim().length > 0;

	return (
		<section
			data-testid="vocab-test-panel"
			className="mt-4 overflow-hidden rounded-xl border border-border/10 bg-(--bg-subtle)"
		>
			<button
				type="button"
				onClick={() => onOpenChange(!open)}
				aria-expanded={open}
				aria-label={t("vocabulary.testTitle")}
				className="flex w-full cursor-pointer items-center gap-2 px-3.5 py-2.5 text-start transition-colors hover:bg-foreground/5 focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
			>
				<HugeiconsIcon
					icon={TestTubeIcon}
					strokeWidth={2}
					aria-hidden="true"
					className="size-4 text-(--text-muted)"
				/>
				<span className="text-sm font-medium text-(--text-primary)">
					{t("vocabulary.testTitle")}
				</span>
				<HugeiconsIcon
					icon={ArrowDown01Icon}
					strokeWidth={2.5}
					aria-hidden="true"
					className={cn(
						"ms-auto size-3.5 text-(--text-muted) transition-transform duration-150",
						open && "rotate-180",
					)}
				/>
			</button>
			{open && (
				<div className="space-y-2 border-t border-border/10 px-3.5 py-3">
					<Input
						value={query}
						onChange={(e) => onQueryChange(e.target.value)}
						placeholder={t("vocabulary.testPlaceholder")}
						aria-label={t("vocabulary.testPlaceholder")}
						className="w-full rounded-xl bg-(--bg) border-border/10"
					/>
					{active && (
						<div
							data-testid="vocab-test-output"
							className={cn(
								"rounded-xl border px-3 py-2 text-sm",
								applied
									? "border-emerald-400/30 bg-emerald-400/10 text-(--text-primary)"
									: "border-border/10 bg-(--bg) text-(--text-muted)",
							)}
						>
							<div className="mb-0.5 flex items-center justify-between gap-2">
								<p className="text-[11px] font-semibold uppercase tracking-wider text-(--text-muted)">
									{t("vocabulary.testCorrected")}
								</p>
								{pending && (
									<p
										data-testid="vocab-test-pending"
										className="text-[11px] text-(--text-muted) italic"
									>
										{t("vocabulary.testPending")}
									</p>
								)}
							</div>
							<p className="whitespace-pre-wrap break-words">{output}</p>
						</div>
					)}
					{active && !applied && !usingFallback && (
						<p className="text-xs text-(--text-muted)">
							{t("vocabulary.testNoChange")}
						</p>
					)}
					{active && usingFallback && (
						<p
							data-testid="vocab-test-fallback"
							className="text-xs font-medium text-amber-700 dark:text-amber-400"
						>
							{t("vocabulary.testFallbackNote")}
						</p>
					)}
				</div>
			)}
		</section>
	);
}
