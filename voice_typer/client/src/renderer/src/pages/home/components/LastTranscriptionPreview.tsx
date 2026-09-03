import {
	AlertCircleIcon,
	ClipboardPasteIcon,
	Copy01Icon,
	Delete01Icon,
	Mic01Icon,
	Undo02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo, useCallback, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { TranscriptionQualitySummary } from "@/types/ipc";
import { isLowConfidenceQuality } from "../lib/quality";

/**
 * Transcriptions longer than this render clamped (two lines) with a
 * show-more / show-less toggle. Roughly the capacity of the card's
 * two display lines at its fixed width — copy / re-paste / undo always
 * operate on the FULL text regardless of the collapsed display.
 */
const LONG_TEXT_THRESHOLD = 160;

/**
 * Preview card for the most recent transcription. Offers Copy (clipboard
 * write of the full transcription), Undo (sends backspaces to the
 * previously-pasted field), Re-paste (re-executes the paste of the same
 * text) and — when wired — Discard (removes the ephemeral preview card).
 *
 * When the engine-reported quality summary (Whisper batch path only)
 * flags a low-confidence decoding, an inline warning is rendered above
 * the actions with a Re-dictate affordance that starts a fresh
 * recording through the same toggle-dictation path as the mic button.
 *
 *  / : extracted from Home.tsx so the page file stays a
 * thin composition root. Behaviour + props are preserved byte-for-byte.
 */
export interface LastTranscriptionPreviewProps {
	text: string;
	onUndo: () => void;
	onRepaste: () => void;
	/** Per-dictation confidence summary from the `transcription_final`
	 *  push event. Absent for engines without per-segment stats — the
	 *  warning is then never shown. */
	quality?: TranscriptionQualitySummary | null;
	/** Starts a new recording (same mechanism as the mic button).
	 *  The Re-dictate affordance renders only when both this and a
	 *  low-confidence `quality` are present. */
	onRedictate?: () => void;
	/** Removes the preview card (clears the ephemeral local preview —
	 *  it does NOT touch persisted history). Rendered only when
	 *  provided. */
	onDiscard?: () => void;
}

export function LastTranscriptionPreview({
	text,
	onUndo,
	onRepaste,
	quality,
	onRedictate,
	onDiscard,
}: LastTranscriptionPreviewProps) {
	const lowConfidence = isLowConfidenceQuality(quality);
	const [expanded, setExpanded] = useState(false);
	const isLong = text.length > LONG_TEXT_THRESHOLD;

	const handleCopy = useCallback(async () => {
		try {
			// Always the FULL transcription — the collapsed two-line
			// display is a presentation choice and must not truncate
			// what lands on the clipboard.
			await navigator.clipboard.writeText(text);
			toast.success(t("history.copiedToClipboard"));
		} catch (err) {
			console.error("[renderer:LastTranscriptionPreview] copy failed:", err);
			toast.error(t("activityList.failedToCopy"));
		}
	}, [text]);

	return (
		// The ancestor `<output aria-live="polite">` wrapper in Home.tsx
		// is the single live region for this card. Carrying a second
		// `aria-live="polite"` here would cause screen readers to
		// announce the same text twice, so it is intentionally omitted.
		<div className="w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3">
			<p
				className={cn(
					"overflow-hidden text-ellipsis text-[0.8125rem] text-(--text-muted)",
					!expanded && "line-clamp-2",
				)}
			>
				{text}
			</p>
			{isLong && (
				<button
					type="button"
					data-testid="last-transcription-show-toggle"
					aria-expanded={expanded}
					onClick={() => setExpanded((v) => !v)}
					className="mt-1 cursor-pointer text-xs text-(--text-muted) transition-colors hover:text-(--text-primary)"
				>
					{expanded ? t("home.showLess") : t("home.showMore")}
				</button>
			)}
			{lowConfidence && (
				<div className="mt-2 flex items-center justify-between gap-3 rounded-md border border-warning/30 bg-warning/10 px-2.5 py-1.5">
					<span className="flex min-w-0 items-start gap-2 text-warning">
						<HugeiconsIcon
							icon={AlertCircleIcon}
							strokeWidth={2}
							className="mt-0.5 h-3.5 w-3.5 shrink-0"
							aria-hidden="true"
						/>
						<span className="text-xs leading-snug">
							{t("home.lowConfidenceWarning")}
						</span>
					</span>
					{onRedictate && (
						<Button
							variant="ghost"
							size="sm"
							onClick={onRedictate}
							title={t("home.redictateAria")}
							aria-label={t("home.redictateAria")}
							className="shrink-0 gap-2 text-xs text-warning hover:text-warning/80"
						>
							<HugeiconsIcon
								icon={Mic01Icon}
								strokeWidth={2}
								className="h-3.5 w-3.5"
							/>
							{t("home.redictate")}
						</Button>
					)}
				</div>
			)}
			<div className="mt-2 flex items-center justify-end gap-1">
				<Button
					variant="ghost"
					size="sm"
					onClick={handleCopy}
					disabled={!text}
					title={t("home.copyAria")}
					aria-label={t("home.copyAria")}
					data-testid="last-transcription-copy"
					className="gap-2 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Copy01Icon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{t("home.copy")}
				</Button>
				<Button
					variant="ghost"
					size="sm"
					onClick={onUndo}
					disabled={!text}
					title={t("home.undoAria")}
					aria-label={t("home.undoAria")}
					className="gap-2 text-xs text-(--text-muted) hover:text-(--text-primary)"
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
					className="gap-2 text-xs text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={ClipboardPasteIcon}
						strokeWidth={2}
						className="h-3.5 w-3.5"
					/>
					{t("home.repaste")}
				</Button>
				{onDiscard && (
					// Destructive affordance, muted at rest and flipping to the
					// solid destructive treatment on hover (same hover contract
					// as the app's destructive confirm action) so the wipe reads
					// as danger without competing with the row's neutral
					// actions at rest. The dark:hover restatement is REQUIRED:
					// the ghost variant's dark:hover:bg-muted/50 out-specifies
					// a plain hover:bg-destructive (Tailwind v4
					// `&:is(.dark *)`), so dark mode would hover translucent
					// grey, not solid red.
					<Button
						variant="ghost"
						size="sm"
						onClick={onDiscard}
						disabled={!text}
						title={t("home.discardAria")}
						aria-label={t("home.discardAria")}
						data-testid="last-transcription-discard"
						className="gap-2 text-xs text-(--text-muted) hover:border-destructive hover:bg-destructive hover:text-destructive-foreground dark:hover:bg-destructive"
					>
						<HugeiconsIcon
							icon={Delete01Icon}
							strokeWidth={2}
							className="h-3.5 w-3.5"
						/>
						{t("home.discard")}
					</Button>
				)}
			</div>
		</div>
	);
}

export default memo(LastTranscriptionPreview);
