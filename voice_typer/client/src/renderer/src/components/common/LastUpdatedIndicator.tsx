import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

interface LastUpdatedIndicatorProps {
	/** Localized relative label, e.g. "5s ago" or "Just now" (from useLastUpdated). */
	agoLabel: string;
	/** Refresh callback, calls the page's `load*` function. */
	onRefresh: () => void;
	/** True while the refresh is in-flight (disables the button + shows a spinner). */
	refreshing?: boolean;
	/** Optional className override for the wrapping container. */
	className?: string;
}

export function LastUpdatedIndicator({
	agoLabel,
	onRefresh,
	refreshing = false,
	className,
}: LastUpdatedIndicatorProps) {
	return (
		<div
			className={cn(
				"flex items-center gap-2 text-xs text-(--text-muted)",
				className,
			)}
			data-testid="last-updated-indicator"
		>
			{/* The timestamp/label is the dynamic part, it lives inside its
			    own polite live region so screen readers announce updates
			    ("Last updated 5s ago" → "10s ago") without re-announcing
			    the refresh button. The button stays OUTSIDE the live
			    region (a button inside a live region would be announced
			    twice). */}
			<span aria-live="polite">
				<span>{t("common.lastUpdatedWithValue", { value: agoLabel })}</span>
			</span>
			{/* While a refresh is in flight the SAME icon spins in place
			    (animate-spin on the unchanged h-3.5 glyph). Swapping in a
			    different element here (e.g. a border-2 Spinner at a
			    different box size) reads as a size/color jump on every
			    click; rotating the mounted icon keeps the box, stroke,
			    and color identical so the only motion is the rotation.
			    The button stays disabled while refreshing (muted +
			    pointer-events-none per the Button base), and its
			    aria-label/title are untouched. */}
			<Button
				variant="ghost"
				size="icon-xs"
				onClick={onRefresh}
				disabled={refreshing}
				aria-label={t("common.refreshAria")}
				title={t("common.refreshAria")}
			>
				<HugeiconsIcon
					icon={RefreshIcon}
					strokeWidth={1.625}
					aria-hidden="true"
					className={cn("h-3.5 w-3.5", refreshing && "animate-spin")}
				/>
			</Button>
		</div>
	);
}
