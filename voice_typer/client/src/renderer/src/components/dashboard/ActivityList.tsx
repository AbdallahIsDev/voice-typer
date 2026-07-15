import {
	Copy01Icon,
	Delete01Icon,
	StarIcon,
	Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import type { HistoryRecord } from "@/types/ipc";

function formatTimestamp(ts: string): string {
	try {
		const d = new Date(ts);
		return (
			d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
			" · " +
			d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
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

export default function ActivityList({
	items,
	lineClamp = 2,
	title = t("home.recentActivity"),
	showViewAll = false,
	onViewAll,
	onDelete,
	onToggleFavorite,
}: ActivityListProps) {
	const [copiedId, setCopiedId] = useState<number | null>(null);
	// NEW-TS-020: track copy timeout in a ref and clear on unmount
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
			// NEW-TS-020: clear previous timeout before setting new one
			if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
			copyTimeoutRef.current = setTimeout(() => setCopiedId(null), 2000);
		} catch {
			toast.error(t("activityList.failedToCopy"));
		}
	}, []);

	const handleDelete = useCallback(
		(id: number) => {
			onDelete?.(id);
			// Don't show premature success toast — parent handles feedback
		},
		[onDelete],
	);

	const handleFavorite = useCallback(
		(id: number) => {
			onToggleFavorite?.(id);
		},
		[onToggleFavorite],
	);

	if (items.length === 0) return null;

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
			<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
				{" "}
				{items.map((item) => {
					const handleItemFavorite = () => handleFavorite(item.id);
					const handleItemCopy = () => handleCopy(item);
					const handleItemDelete = () => handleDelete(item.id);
					return (
						<div key={item.id} className="flex items-start gap-3 px-3.5 py-2.5">
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
								{onToggleFavorite && (
									<Button
										variant="ghost"
										size="icon-xs"
										onClick={handleItemFavorite}
										className="shrink-0 text-(--text-muted) hover:text-amber-400"
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
											className={`h-4 w-4 ${item.favorite ? "text-amber-400" : ""}`}
										/>
									</Button>
								)}
								<Button
									variant="ghost"
									size="icon-xs"
									onClick={handleItemCopy}
									className="shrink-0 text-(--text-muted) hover:text-(--text-primary)"
									title={t("history.copyText")}
									aria-label={t("history.copyText")}
								>
									{copiedId === item.id ? (
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
								{onDelete && (
									<Button
										variant="ghost"
										size="icon-xs"
										onClick={handleItemDelete}
										className="shrink-0 text-(--text-muted) hover:text-red-400"
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
				})}
			</div>
		</div>
	);
}
