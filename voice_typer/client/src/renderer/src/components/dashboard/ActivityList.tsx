import {
	Copy01Icon,
	Delete01Icon,
	StarIcon,
	Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { getLocale, t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

function formatTimestamp(ts: string): string {
	try {
		const d = new Date(ts);
		//pass the user-selected UI locale (not the browser
		// default) so dates/times respect the user's language choice.
		return (
			d.toLocaleDateString(getLocale(), { month: "short", day: "numeric" }) +
			" · " +
			d.toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit" })
		);
	} catch {
		return ts;
	}
}

interface ActivityListProps {
	items: HistoryRecord[];
	lineClamp?: number;
	title?: string;
	showViewAll?: boolean;
	onViewAll?: () => void;
	onDelete?: (id: number) => void;
	onToggleFavorite?: (id: number) => void;
}

// ── ActivityListRow ────────────────────────────────────────────────────
//
// Extracted from the inline `.map()` body in ActivityList and
// wrapped in `React.memo`. Previously, every render of ActivityList
// allocated 3 fresh closure functions per row (`handleItemFavorite`,
// `handleItemCopy`, `handleItemDelete`) — on a 200-row dashboard list
// that's 600 closure allocations per copy/favorite click (because the
// click flips `copiedId`, re-rendering the parent and rebuilding every
// row's handlers).
//
// The memo'd row receives:
//   - `item`              — the HistoryRecord (stable reference unless the
//                           underlying record changes)
//   - `copied`            — a primitive boolean (true when this row is the
//                           copied one) instead of the parent's `copiedId`
//                           so only the row whose copied-state actually
//                           changed re-renders
//   - `lineClamp`         — primitive number
//   - `onCopy`            — stable useCallback from parent (receives the
//                           item, so the row's onClick can pass it through)
//   - `onDelete`          — stable useCallback from parent (or undefined)
//   - `onToggleFavorite`  — stable useCallback from parent (or undefined)
//
// All non-primitive props are stable useCallbacks from the parent, so
// `memo`'s default shallow-equal comparator skips re-renders for every
// row except the one whose `copied` flag actually toggled.
interface ActivityListRowProps {
	item: HistoryRecord;
	copied: boolean;
	lineClamp: number;
	onCopy: (item: HistoryRecord) => void;
	onDelete?: (id: number) => void;
	onToggleFavorite?: (id: number) => void;
}

const ActivityListRow = memo(function ActivityListRow({
	item,
	copied,
	lineClamp,
	onCopy,
	onDelete,
	onToggleFavorite,
}: ActivityListRowProps) {
	return (
		<div className="flex items-start gap-3 px-3.5 py-2.5">
			<div className="flex flex-col gap-1 flex-1 min-w-0">
				<p
					className="text-sm text-(--text-primary) leading-snug overflow-hidden text-ellipsis"
					style={{
						display: "-webkit-box",
						WebkitLineClamp: lineClamp,
						WebkitBoxOrient: "vertical",
					}}
				>
					{item.text}
				</p>
				<span className="text-xs text-(--text-muted) block">
					{formatTimestamp(item.timestamp)}
					{item.word_count != null && (
						<>
							<span className="mx-1">·</span>
							{t("activityList.wordsCount", {
								count: String(item.word_count),
							})}
						</>
					)}
				</span>
			</div>
			<div className="flex items-center gap-1">
				{/* Action order: Copy first (copying a past transcription is
				    the primary reason a user opens History), then
				    Star/Favorite, then Delete LAST — destructive actions
				    never lead the group. */}
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={() => onCopy(item)}
					className="shrink-0 text-(--text-muted) hover:text-(--text-primary)"
					title={t("history.copyText")}
					aria-label={t("history.copyText")}
				>
					{copied ? (
						<HugeiconsIcon
							icon={Tick02Icon}
							strokeWidth={2.5}
							className="h-4 w-4"
						/>
					) : (
						<HugeiconsIcon
							icon={Copy01Icon}
							strokeWidth={2.5}
							className="h-4 w-4"
						/>
					)}
				</Button>
				{onToggleFavorite && (
					<Button
						variant="ghost"
						size="icon-xs"
						onClick={() => onToggleFavorite(item.id)}
						className="shrink-0 text-(--text-muted) hover:text-warning"
						title={
							item.favorite
								? t("activityList.removeFromFavorites")
								: t("activityList.addToFavorites")
						}
						aria-label={
							item.favorite
								? t("activityList.removeFromFavorites")
								: t("activityList.addToFavorites")
						}
					>
						<HugeiconsIcon
							icon={StarIcon}
							strokeWidth={2.5}
							className={`h-4 w-4 ${item.favorite ? "text-warning" : ""}`}
						/>
					</Button>
				)}
				{onDelete && (
					<Button
						variant="ghost"
						size="icon-xs"
						onClick={() => onDelete(item.id)}
						className="shrink-0 text-(--text-muted) hover:text-destructive"
						title={t("common.delete")}
						aria-label={t("history.deleteEntry")}
					>
						<HugeiconsIcon
							icon={Delete01Icon}
							strokeWidth={2.5}
							className="h-4 w-4"
						/>
					</Button>
				)}
			</div>
		</div>
	);
});

function ActivityListInner({
	items,
	lineClamp = 2,
	title = t("home.recentActivity"),
	showViewAll = false,
	onViewAll,
	onDelete,
	onToggleFavorite,
}: ActivityListProps) {
	const [copiedId, setCopiedId] = useState<number | null>(null);
	//track copy timeout in a ref and clear on unmount
	const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	useEffect(() => {
		return () => {
			if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
		};
	}, []);

	const handleCopy = useCallback(async (item: HistoryRecord) => {
		try {
			await navigator.clipboard.writeText(item.text);
			setCopiedId(item.id);
			toast.success(t("history.copiedToClipboard"));
			//clear previous timeout before setting new one
			if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
			copyTimeoutRef.current = setTimeout(() => setCopiedId(null), 2000);
		} catch {
			toast.error(t("activityList.failedToCopy"));
		}
	}, []);

	// The previous `handleDelete` / `handleFavorite` pass-through
	// wrappers have been removed — the memo'd `ActivityListRow` now calls
	// `onDelete(item.id)` / `onToggleFavorite(item.id)` directly. Both
	// parent callbacks are already stable (passed in as props), so the
	// row's `memo` shallow-equal comparator keeps them referentially
	// equal across re-renders.

	// Previously returned ``null`` when ``items`` was empty,
	// which meant a parent rendering ``<ActivityList items={[]} />``
	// (e.g. the Home page before any dictation has happened) showed
	// nothing at all — no heading, no "no recent activity" hint, just
	// blank space.  We now render an inline muted message so the user
	// knows the section exists but has no entries yet.  The message is
	// rendered inside the same ``rounded-lg border`` container as a
	// populated list so the visual rhythm is preserved.
	if (items.length === 0) {
		return (
			<div className="w-full mt-4">
				<div className="flex items-center justify-between w-full mb-2.5">
					<span className="text-[12px] font-semibold text-(--text-primary)">
						{title}
					</span>
					{showViewAll && onViewAll && (
						<Button
							onClick={onViewAll}
							variant="link"
							size="xs"
							className="text-[12px] font-semibold text-(--text-muted) hover:text-(--text-primary) p-0"
						>
							{t("activityList.viewAll")}
						</Button>
					)}
				</div>
				<div className="rounded-lg border border-border/5 bg-(--bg-subtle)">
					<p className="px-3.5 py-4 text-xs text-(--text-muted) text-center">
						{t("activityList.noRecentActivity")}
					</p>
				</div>
			</div>
		);
	}

	return (
		<div className="w-full mt-4">
			<div className="flex items-center justify-between w-full mb-2.5">
				<span className="text-[12px] font-semibold text-(--text-primary)">
					{title}
				</span>
				{showViewAll && onViewAll && (
					<Button
						onClick={onViewAll}
						variant="link"
						size="xs"
						className="text-[12px] font-semibold text-(--text-muted) hover:text-(--text-primary) p-0"
					>
						{t("activityList.viewAll")}
					</Button>
				)}
			</div>
			<div className="rounded-lg border border-border/5 bg-(--bg-subtle) divide-y divide-border/5">
				{" "}
				{items.map((item) => (
					<ActivityListRow
						key={item.id}
						item={item}
						copied={copiedId === item.id}
						lineClamp={lineClamp}
						onCopy={handleCopy}
						onDelete={onDelete}
						onToggleFavorite={onToggleFavorite}
					/>
				))}
			</div>
		</div>
	);
}

//wrap in React.memo so the list doesn't re-render on every parent
// re-render when its props haven't changed. The non-primitive props
// (`items`, `onDelete`, `onToggleFavorite`, `onViewAll`) are stable
// references from the parent (Home.tsx now wraps `onViewAll` in
// `useCallback`; `items` is the `recent` array whose identity is
// preserved by `useAppStore` selectors; `onDelete` /
// `onToggleFavorite` are not passed by Home so they're `undefined`,
// which memo treats as equal). The default shallow-equal comparator
// (matching the TitleBar.tsx:324 pattern) skips re-renders until the
// actual list contents or callbacks change.
export default memo(ActivityListInner);
