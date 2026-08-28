import { RefreshIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";

/**
 * LastUpdatedIndicator — small "Last updated Xs ago [refresh]" widget
 * rendered near the top of each page that keeps a module-level cache
 * (Home, History, Models, Microphone, Dashboard).
 *
 * The cache is only refreshed by explicit
 * user action, the `transcription_final` push event, or the
 * `config_changed` event. If the backend state changes through any
 * other path while the renderer is open, the next navigation shows
 * stale data. This widget makes the staleness visible (so users know
 * the data may be out of date) and offers a one-click manual refresh.
 *
 * Visual treatment is intentionally subtle: small text + small ghost
 * icon button, so it doesn't compete with the page's primary content.
 * The label is `(--text-muted)` (low-contrast) and the refresh button
 * is a ghost variant so it blends into the page chrome.
 */
interface LastUpdatedIndicatorProps {
	/** Localized relative label, e.g. "5s ago" or "Just now" (from useLastUpdated). */
	agoLabel: string;
	/** Refresh callback — calls the page's `load*` function. */
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
				"flex items-center gap-1.5 text-xs text-(--text-muted)",
				className,
			)}
			data-testid="last-updated-indicator"
		>
			{/* The timestamp/label is the dynamic part — it lives inside its
			    own polite live region so screen readers announce updates
			    ("Last updated 5s ago" → "10s ago") without re-announcing
			    the refresh button. The button stays OUTSIDE the live
			    region (a button inside a live region would be announced
			    twice). */}
			<span aria-live="polite">
				<span>{t("common.lastUpdatedWithValue", { value: agoLabel })}</span>
			</span>
			<Button
				variant="ghost"
				size="icon"
				onClick={onRefresh}
				disabled={refreshing}
				aria-label={t("common.refreshAria")}
				title={t("common.refreshAria")}
			>
				{refreshing ? (
					// Decorative — the parent <Button aria-label>
					// already supplies the accessible name; a nested
					// role="img" aria-label="Loading" would compete with
					// (and re-announce over) the button's own label.
					<Spinner decorative className="border-current h-3 w-3" />
				) : (
					<HugeiconsIcon
						icon={RefreshIcon}
						strokeWidth={1.625}
						className="h-3.5 w-3.5"
					/>
				)}
			</Button>
		</div>
	);
}
