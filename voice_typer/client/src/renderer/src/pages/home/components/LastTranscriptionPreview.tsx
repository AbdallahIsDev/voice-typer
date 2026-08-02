import { ClipboardPasteIcon, Undo02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

/**
 * Preview card for the most recent transcription. Offers Undo (sends
 * backspaces to the previously-pasted field) and Re-paste (re-executes
 * the paste of the same text).
 *
 *  / : extracted from Home.tsx so the page file stays a
 * thin composition root. Behaviour + props are preserved byte-for-byte.
 */
export interface LastTranscriptionPreviewProps {
	text: string;
	onUndo: () => void;
	onRepaste: () => void;
}

export function LastTranscriptionPreview({
	text,
	onUndo,
	onRepaste,
}: LastTranscriptionPreviewProps) {
	return (
		<div
			className="w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3"
			// QV-96: the preview container itself carries aria-live="polite"
			// so the card remains accessible even when rendered outside
			// Home's <output> wrapper.
			aria-live="polite"
		>
			<p className="line-clamp-2 overflow-hidden text-ellipsis text-[13px] text-(--text-muted)">
				{text}
			</p>
			<div className="mt-2 flex items-center justify-end gap-1">
				<Button
					variant="ghost"
					size="sm"
					onClick={onUndo}
					disabled={!text}
					title={t("home.undoAria")}
					aria-label={t("home.undoAria")}
					className="gap-1.5 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Undo02Icon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{t("home.undo")}
				</Button>
				<Button
					variant="ghost"
					size="sm"
					onClick={onRepaste}
					disabled={!text}
					title={t("home.repasteAria")}
					aria-label={t("home.repasteAria")}
					className="gap-1.5 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={ClipboardPasteIcon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{t("home.repaste")}
				</Button>
			</div>
		</div>
	);
}

export default LastTranscriptionPreview;
