import {
	Copy01Icon,
	Delete01Icon,
	StarIcon,
	Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	memo,
	type KeyboardEvent as ReactKeyboardEvent,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { toast } from "sonner";
import {
	formatRecordTime,
	groupRecordsByDate,
} from "@/components/dashboard/historyGroups";
import { Button } from "@/components/ui/button";
import { getLocale, t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

/**
 * Transcriptions longer than this are treated as potentially clamped by
 * the row's line clamp, so the text becomes click-to-expand (when the
 * parent supplies ``onFetchFullText``). Mirrors the threshold used by
 * the Home preview card. Rows ALSO become expandable when the backend
 * flagged the 500-char preview via ``text_truncated``, regardless of
 * length.
 */
const EXPAND_TEXT_LENGTH_THRESHOLD = 160;

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
	/**
	 * Group the list into one separate card per date ("Today" /
	 * Yesterday / long date as the card header). Rows then show only
	 * their TIME — the date lives in the card header. Only meaningful
	 * for chronologically-sorted lists; the History page disables
	 * grouping for alphabetical sorts.
	 */
	groupByDate?: boolean;
	/**
	 * Fetch the FULL text of a record by id (the list payload carries a
	 * 500-char preview). When provided, clamped rows become
	 * click-to-expand (keyboard operable, ``aria-expanded`` state).
	 * Resolve with the full text, or ``null`` on failure.
	 */
	onFetchFullText?: (id: number) => Promise<string | null>;
	/**
	 * Hide the section header row (title + "View all"). The History page
	 * renders the list directly under its toolbar — a "Recent Activity"
	 * heading there would duplicate the page title.
	 */
	hideHeader?: boolean;
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
//   - `grouped`           — primitive boolean (date-grouped list mode:
//                           rows show time only)
//   - `onCopy`            — stable useCallback from parent (receives the
//                           item, so the row's onClick can pass it through)
//   - `onDelete`          — stable useCallback from parent (or undefined)
//   - `onToggleFavorite`  — stable useCallback from parent (or undefined)
//   - `onFetchFullText`   — stable useCallback from parent (or undefined)
//
// All non-primitive props are stable useCallbacks from the parent, so
// `memo`'s default shallow-equal comparator skips re-renders for every
// row except the one whose `copied` flag actually toggled.
interface ActivityListRowProps {
	item: HistoryRecord;
	copied: boolean;
	lineClamp: number;
	grouped: boolean;
	onCopy: (item: HistoryRecord) => void;
	onDelete?: (id: number) => void;
	onToggleFavorite?: (id: number) => void;
	onFetchFullText?: (id: number) => Promise<string | null>;
}

const ActivityListRow = memo(function ActivityListRow({
	item,
	copied,
	lineClamp,
	grouped,
	onCopy,
	onDelete,
	onToggleFavorite,
	onFetchFullText,
}: ActivityListRowProps) {
	const [expanded, setExpanded] = useState(false);
	// Full text fetched on demand. Local to the row so expanding never
	// churns the parent's records array (and survives background list
	// refreshes — the record id is stable). ``null`` until fetched.
	const [fullText, setFullText] = useState<string | null>(null);
	const [loadingText, setLoadingText] = useState(false);

	const displayedText = fullText ?? item.text;
	// A row is expandable when the parent can supply full text AND the
	// text is (or may be) clamped: either the backend flagged the
	// 500-char preview, or the text exceeds the length where the line
	// clamp kicks in. Short rows stay inert — no cursor, no hover, no
	// button semantics (hover states only where a genuine click action
	// exists).
	const expandable =
		!!onFetchFullText &&
		(item.text_truncated === true ||
			item.text.length > EXPAND_TEXT_LENGTH_THRESHOLD);

	const toggleExpanded = useCallback(async () => {
		if (!onFetchFullText || loadingText) return;
		if (expanded) {
			setExpanded(false);
			return;
		}
		// First expansion of a backend-truncated row: fetch the FULL text
		// (the list payload carries only the 500-char preview). An empty
		// result means the row is gone (or the fetch failed) — surface a
		// toast instead of expanding to a clipped preview.
		if (item.text_truncated === true && fullText === null) {
			setLoadingText(true);
			try {
				const text = await onFetchFullText(item.id);
				if (text == null || text === "") {
					toast.error(t("activityList.loadTextFailed"));
					return;
				}
				setFullText(text);
			} catch {
				toast.error(t("activityList.loadTextFailed"));
				return;
			} finally {
				setLoadingText(false);
			}
		}
		setExpanded(true);
	}, [
		onFetchFullText,
		loadingText,
		expanded,
		item.text_truncated,
		item.id,
		fullText,
	]);

	const handleTextKeyDown = useCallback(
		(e: ReactKeyboardEvent<HTMLDivElement>) => {
			if (e.key === "Enter" || e.key === " ") {
				e.preventDefault();
				void toggleExpanded();
			}
		},
		[toggleExpanded],
	);

	return (
		// Row layout: the action cluster is vertically CENTERED against
		// the full row height (items-center), not top-pinned. Entry text
		// wraps to 1–3 lines, so top alignment leaves dead space under
		// the icons on taller rows; centering distributes the cluster
		// within whatever height the row ends up being, with no overlap
		// of the text itself.
		<div className="flex items-center gap-3 px-4 py-2">
			<div className="flex min-w-0 flex-1 flex-col gap-1">
				{/* Positioning context for the collapsed fade overlay. */}
				<div className="relative min-w-0">
					{/* The text block doubles as the expand/collapse control
					    when the row can reveal more: clicking anywhere on the
					    text toggles, Enter/Space activate it from keyboard
					    focus, and aria-expanded exposes the disclosure state.
					    There is deliberately NO hover wash behind the text —
					    the truncated line already carries the inline fade +
					    "Show more" affordance below, so a background box
					    would just add noise. Action buttons are OUTSIDE this
					    block, so their click targets never collide with it.
					    Not a native <button>: transcript text must stay
					    mouse-selectable, so the disclosure semantics are
					    carried by role/tabIndex/aria-expanded with explicit
					    keyboard activation instead. */}
					{/* biome-ignore lint/a11y/noStaticElementInteractions: the block IS the disclosure control (see comment above) — text selection inside a native <button> is blocked by the UA stylesheet. */}
					{/* biome-ignore lint/a11y/useAriaPropsSupportedByRole: aria-expanded is the disclosure state; the conditional undefined keeps it off inert rows. */}
					<div
						role={expandable ? "button" : undefined}
						tabIndex={expandable ? 0 : undefined}
						aria-expanded={expandable ? expanded : undefined}
						data-testid={expandable ? "activity-row-text-toggle" : undefined}
						onClick={expandable ? () => void toggleExpanded() : undefined}
						onKeyDown={expandable ? handleTextKeyDown : undefined}
						className={`rounded-md transition-colors ${
							expandable
								? "cursor-pointer focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-hidden"
								: ""
						} ${loadingText ? "opacity-60" : ""}`}
					>
						<p
							className="text-sm text-(--text-primary) leading-snug overflow-hidden text-ellipsis"
							style={
								expanded
									? undefined
									: {
											display: "-webkit-box",
											WebkitLineClamp: lineClamp,
											WebkitBoxOrient: "vertical",
										}
							}
						>
							{displayedText}
							{/* Inline collapse affordance at the end of the
							    expanded text — same treatment as the "Show
							    more" reveal (muted inline text, no separate
							    row, no extra vertical space). Nested inside
							    the toggle block, so it stops propagation to
							    avoid double-toggling. */}
							{expandable && expanded && (
								<>
									{" "}
									<button
										type="button"
										aria-expanded={expanded}
										aria-label={t("home.showLess")}
										onClick={(e) => {
											e.stopPropagation();
											void toggleExpanded();
										}}
										onKeyDown={(e) => e.stopPropagation()}
										className="cursor-pointer whitespace-nowrap text-sm leading-snug text-(--text-muted) transition-colors hover:text-(--text-primary) focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden rounded-sm"
									>
										{t("home.showLess")}
									</button>
								</>
							)}
						</p>
					</div>
					{/* Inline masked reveal over the truncated end of the
					    last visible line (collapsed expandable rows only).
					    A small right-anchored overlay fades the clipped
					    text out (transparent → card background) with the
					    "Show more" control sitting on the solid end of the
					    fade, inline with the text line — no separate button
					    row, no extra vertical space, no horizontal padding
					    stolen from the text. The wrapper is
					    pointer-events-none so text selection and clicks
					    pass through everywhere except the real <button>
					    itself (which stops propagation: it lives inside the
					    toggle block and must not double-toggle). */}
					{expandable && !expanded && (
						<div className="pointer-events-none absolute end-0 bottom-0 flex items-center bg-gradient-to-r from-transparent to-(--bg-subtle) ps-10 rtl:bg-gradient-to-l">
							<button
								type="button"
								aria-expanded={expanded}
								aria-label={t("home.showMore")}
								onClick={(e) => {
									e.stopPropagation();
									void toggleExpanded();
								}}
								onKeyDown={(e) => e.stopPropagation()}
								className="pointer-events-auto cursor-pointer whitespace-nowrap text-sm leading-snug text-(--text-muted) transition-colors hover:text-(--text-primary) focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden rounded-sm"
							>
								{loadingText ? t("history.loading") : t("home.showMore")}
							</button>
						</div>
					)}
				</div>
				<span className="text-xs text-(--text-muted) block">
					{grouped
						? formatRecordTime(item.timestamp)
						: formatTimestamp(item.timestamp)}
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
				    never lead the group. Copy always receives the DISPLAYED
				    text, so an expanded row copies the full transcript. */}
				<Button
					variant="ghost"
					size="icon-xs"
					onClick={() => onCopy({ ...item, text: displayedText })}
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
	groupByDate = false,
	onFetchFullText,
	hideHeader = false,
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
			// No top margin here (or below): vertical rhythm comes from
			// the PARENT's gap — a margin on this root would stack with
			// it and double the space above the card.
			<div className="flex w-full flex-col gap-2.5">
				{!hideHeader && (
					<div className="flex items-center justify-between w-full">
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
				)}
				<div className="rounded-lg border border-border/5 bg-(--bg-subtle)">
					<p className="px-3.5 py-4 text-xs text-(--text-muted) text-center">
						{t("activityList.noRecentActivity")}
					</p>
				</div>
			</div>
		);
	}

	// Date-grouped mode: chunk the (already-sorted) items into per-day
	// sections. Flat mode renders the list exactly as before.
	const groups = groupByDate ? groupRecordsByDate(items) : null;

	return (
		<div className="flex w-full flex-col gap-2.5">
			{!hideHeader && (
				<div className="flex items-center justify-between w-full">
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
			)}
			{groups ? (
				// One SEPARATE card per date — the card surface (background,
				// border, rounded corners) matches the flat list's card, and
				// the parent gap between cards makes "new card = new day"
				// readable at a glance without scanning for labels.
				<div className="flex w-full flex-col gap-4">
					{groups.map((group, gi) => (
						<section
							key={group.key || `unknown-date-${gi}`}
							aria-labelledby={
								group.label ? `history-date-${group.key}` : undefined
							}
							className="rounded-lg border border-border/5 bg-(--bg-subtle)"
						>
							{group.label && (
								<div className="px-4 pt-3 pb-1">
									<h3
										id={`history-date-${group.key}`}
										className="text-xs font-semibold tracking-wide text-(--text-muted)"
									>
										{group.label}
									</h3>
								</div>
							)}
							<div className="divide-y divide-border/5">
								{group.records.map((item) => (
									<ActivityListRow
										key={item.id}
										item={item}
										copied={copiedId === item.id}
										lineClamp={lineClamp}
										grouped={!!group.label}
										onCopy={handleCopy}
										onDelete={onDelete}
										onToggleFavorite={onToggleFavorite}
										onFetchFullText={onFetchFullText}
									/>
								))}
							</div>
						</section>
					))}
				</div>
			) : (
				<div className="rounded-lg border border-border/5 bg-(--bg-subtle) divide-y divide-border/5">
					{" "}
					{items.map((item) => (
						<ActivityListRow
							key={item.id}
							item={item}
							copied={copiedId === item.id}
							lineClamp={lineClamp}
							grouped={false}
							onCopy={handleCopy}
							onDelete={onDelete}
							onToggleFavorite={onToggleFavorite}
							onFetchFullText={onFetchFullText}
						/>
					))}
				</div>
			)}
		</div>
	);
}

//wrap in React.memo so the list doesn't re-render on every parent
// re-render when its props haven't changed. The non-primitive props
// (`items`, `onDelete`, `onToggleFavorite`, `onViewAll`,
// `onFetchFullText`) are stable references from the parent (Home.tsx
// wraps `onViewAll` in `useCallback`; `items` is the `recent` array
// whose identity is preserved by `useAppStore` selectors; `onDelete` /
// `onToggleFavorite` are not passed by Home so they're `undefined`,
// which memo treats as equal). The default shallow-equal comparator
// (matching the TitleBar.tsx:324 pattern) skips re-renders until the
// actual list contents or callbacks change.
export default memo(ActivityListInner);
