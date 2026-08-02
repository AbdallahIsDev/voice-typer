import { StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

/**
 * Surface recording errors. Previously `lastError` was tracked in the
 * store but never rendered on Home — errors were invisible to the user
 * (only the status pill colour changed to red). This card renders below
 * the status pill whenever recordingState is "error", showing the
 * backend's error message plus a Retry button.
 *
 *  / : extracted from Home.tsx so the page file stays a
 * thin composition root. Behaviour + props are preserved byte-for-byte.
 */
export interface RecordingErrorCardProps {
	message: string;
	onRetry: () => void;
	retrying: boolean;
	retryLabel?: string;
	/**
	 * Optional secondary "Open Microphone settings" CTA. When
	 * provided, a ghost button is rendered so the user can jump
	 * straight to the Microphone page if the error was caused by a
	 * missing/invalid device — Retry alone is not enough for those
	 * cases.
	 */
	onOpenMicSettings?: () => void;
	micSettingsLabel?: string;
}

// Hard-coded English fallback so the secondary CTA is usable even
// though no `home.openMicSettings` i18n key exists yet. Callers (Home.tsx)
// can override via the `micSettingsLabel` prop once a translation key
// is added. Mirrors the precedent set by `AudioSettingsSection.tsx`
// which uses the hard-coded string "Go to Microphone" for the same
// navigation target.
const DEFAULT_MIC_SETTINGS_LABEL = "Open Microphone settings";

export function RecordingErrorCard({
	message,
	onRetry,
	retrying,
	retryLabel = t("home.retry"),
	onOpenMicSettings,
	micSettingsLabel = DEFAULT_MIC_SETTINGS_LABEL,
}: RecordingErrorCardProps) {
	return (
		<div
			role="alert"
			className="flex w-130 max-w-full items-start gap-3 rounded-[10px] border border-destructive/30 bg-destructive/10 px-4 py-3"
		>
			<HugeiconsIcon
				icon={StopIcon}
				strokeWidth={2}
				className="mt-0.5 h-4 w-4 shrink-0 text-destructive"
				aria-hidden
			/>
			<div className="min-w-0 flex-1">
				<p className="text-[13px] font-medium text-destructive">
					{t("home.errorTitle")}
				</p>
				<p className="mt-0.5 line-clamp-3 overflow-hidden text-ellipsis text-[12px] text-(--text-muted)">
					{message}
				</p>
				{onOpenMicSettings && (
					<Button
						variant="ghost"
						size="sm"
						onClick={onOpenMicSettings}
						className="mt-2 gap-1.5 px-0 text-[12px] text-(--text-muted) underline-offset-2 hover:text-(--text-primary) hover:underline"
						aria-label={micSettingsLabel}
					>
						{micSettingsLabel}
					</Button>
				)}
			</div>
			<Button
				variant="outline"
				size="sm"
				onClick={onRetry}
				disabled={retrying}
				className="shrink-0 gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
			>
				{retrying && (
					<span
						aria-hidden
						className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent"
					/>
				)}
				{retryLabel}
			</Button>
		</div>
	);
}

export default RecordingErrorCard;
