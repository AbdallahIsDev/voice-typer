// Duplicate-corrections review banner.
//
// Shown when the loaded list contains two or more entries sharing the
// same wrong phrase (case-insensitive, whitespace-collapsed — the same
// rule the backend enforces on write). This surfaces PRE-EXISTING
// duplicates from before the backend check shipped (e.g. hand-edited
// JSON or double-imports) so the user can resolve them, while the
// backend check prevents new ones going forward. "Remove duplicates"
// collapses each group to its first occurrence and persists the
// cleaned list.
import { Alert01Icon, Cancel01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface VocabDuplicateBannerProps {
	/** Number of extra (duplicate) entries beyond the first per phrase. */
	count: number;
	onRemoveDuplicates: () => void | Promise<void>;
	onDismiss: () => void;
}

export function VocabDuplicateBanner({
	count,
	onRemoveDuplicates,
	onDismiss,
}: VocabDuplicateBannerProps) {
	return (
		<div
			data-testid="vocab-duplicate-banner"
			role="status"
			className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3.5 py-2.5"
		>
			<HugeiconsIcon
				icon={Alert01Icon}
				strokeWidth={2}
				aria-hidden="true"
				className="size-4 shrink-0 text-amber-400"
			/>
			<p className="min-w-0 flex-1 text-xs font-medium text-(--text-primary)">
				{t("vocabulary.duplicateBanner", { count: String(count) })}
			</p>
			<Button
				variant="outline"
				size="sm"
				onClick={onRemoveDuplicates}
				className="gap-1.5 border-amber-400/30 text-xs text-amber-400 hover:border-amber-400/60 hover:bg-amber-400/10 hover:text-amber-300"
			>
				{t("vocabulary.removeDuplicates")}
			</Button>
			<button
				type="button"
				onClick={onDismiss}
				aria-label={t("common.close")}
				title={t("common.close")}
				className="cursor-pointer rounded-lg p-1 text-(--text-muted) transition-colors hover:bg-foreground/10 hover:text-(--text-primary) focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
			>
				<HugeiconsIcon
					icon={Cancel01Icon}
					strokeWidth={2.25}
					aria-hidden="true"
					className="size-4"
				/>
			</button>
		</div>
	);
}
