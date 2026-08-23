import {
	AlertCircleIcon,
	ClipboardPasteIcon,
	Mic01Icon,
	Undo02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import type { TranscriptionQualitySummary } from "@/types/ipc";
import { isLowConfidenceQuality } from "../lib/quality";

/**
 * Preview card for the most recent transcription. Offers Undo (sends
 * backspaces to the previously-pasted field) and Re-paste (re-executes
 * the paste of the same text).
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
}

export function LastTranscriptionPreview({
	text,
	onUndo,
	onRepaste,
	quality,
	onRedictate,
}: LastTranscriptionPreviewProps) {
	const lowConfidence = isLowConfidenceQuality(quality);
	return (
		// The ancestor `<output aria-live="polite">` wrapper in Home.tsx
		// is the single live region for this card. Carrying a second
		// `aria-live="polite"` here would cause screen readers to
		// announce the same text twice, so it is intentionally omitted.
		<div className="w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3">
			<p className="line-clamp-2 overflow-hidden text-ellipsis text-[0.8125rem] text-(--text-muted)">
				{text}
			</p>
			{lowConfidence && (
				<div className="mt-2 flex items-center justify-between gap-3 rounded-md border border-warning/30 bg-warning/10 px-2.5 py-1.5">
					<span className="flex min-w-0 items-start gap-1.5 text-warning">
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
							className="shrink-0 gap-1.5 text-xs text-warning hover:text-warning/80"
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

export default memo(LastTranscriptionPreview);
