import { useT } from "@/i18n/hooks";

interface ShowMoreButtonProps {
	onClick: () => void;
	/** Page-specific test hook (e.g. "templates-show-more"). */
	testid?: string;
}

/**
 * Shared "show more entries" pill for the collection pages (Templates,
 * Vocabulary). One component keeps the visual treatment and the
 * display-cap increment behavior in sync; `testid` differs per page.
 */
export function ShowMoreButton({ onClick, testid }: ShowMoreButtonProps) {
	const t = useT();
	return (
		<button
			type="button"
			data-testid={testid}
			onClick={onClick}
			className="mx-auto flex items-center gap-2 rounded-full border border-border/5 bg-(--bg-subtle) px-4 py-1.5 text-xs font-medium text-accent transition-colors hover:border-accent/40 hover:bg-accent/5 cursor-pointer"
		>
			{t("common.showMore")}
		</button>
	);
}
