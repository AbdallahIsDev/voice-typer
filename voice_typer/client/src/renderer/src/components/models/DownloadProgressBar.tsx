/**
 * DownloadProgressBar — standalone progress display for model downloads.
 *
 * Shows a progress bar with rich status info (downloaded/total, speed, ETA),
 * plus Pause/Resume and Cancel controls. Extracted from Models.tsx so it
 * can be placed independently of the model list.
 *
 * ── XA-13 (sub-agent 15) additions ────────────────────────────────────
 * The original component only handled the "downloading" state — when a
 * download failed, the consumer (useModelLifecycle) unmounted the bar
 * and surfaced the failure via a toast, leaving no in-place error UI.
 * That made it impossible for a user to retry without re-navigating to
 * the model card. The component now supports four optional props that
 * close that gap (all backwards-compatible — existing callers see no
 * behavior change):
 *
 *   • `modelName`        — when provided, the progressbar's aria-label
 *                          becomes "{name} download: {percent}% complete"
 *                          so SR users hear WHICH model is in flight
 *                          (XA-13 priority #4: model-specific messaging).
 *   • `error`            — when set, the bar enters an error state:
 *                          the fill turns red, the status <p> switches
 *                          to a `role="alert"` region announcing the
 *                          failure, and the Pause button is disabled
 *                          (XA-13-M5: no partial-download / error state
 *                          existed before).
 *   • `onRetry`          — when provided AND `error` is set, a "Retry"
 *                          button renders next to Cancel so the user
 *                          can recover in place (XA-13 priority #3).
 *   • `isPaused`         — already existed; now ALSO drives the
 *                          `models.progress.paused` chip in the status
 *                          line so the paused state is visually
 *                          announced (XA-13-M8: the i18n key was
 *                          defined but never rendered).
 *
 * New i18n keys consumed (to be added by primary agent in en.json):
 *   • models.download.errorMessage        — "Download failed: {error}"
 *   • models.download.errorMessageWithName — "{name} download failed: {error}"
 *   • models.download.retry               — "Retry"
 *   • models.download.retryAria           — "Retry download"
 *   • models.download.progressAriaWithName — "{name} download: {percent}% complete"
 * Existing keys reused:
 *   • models.download.progressAria, models.progress.paused,
 *     models.download.pause, models.download.resume,
 *     models.download.pauseAria, models.download.resumeAria,
 *     models.download.cancel, models.download.cancelAria,
 *     models.progress.eta
 */
import { PauseIcon, PlayIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface DownloadProgressBarProps {
	progress: number;
	status: string;
	isPaused: boolean;
	downloadedBytes: number | null;
	totalBytes: number | null;
	speedBps: number | null;
	etaSeconds: number | null;
	onTogglePause: () => void;
	onCancel: () => void;
	/** XA-13 priority #4: optional model name. When provided, the
	 * progressbar's aria-label includes the model name so SR users
	 * hear WHICH model is in flight. */
	modelName?: string;
	/** XA-13-M5: when set, the bar enters the error state. The fill
	 * turns red, the status <p> switches to a role="alert" region,
	 * and the Pause button is disabled. */
	error?: string | null;
	/** XA-13 priority #3: when provided AND `error` is set, a "Retry"
	 * button renders next to Cancel. */
	onRetry?: () => void;
}

function formatBytes(bytes: number | null | undefined): string {
	if (bytes == null || bytes < 0 || !Number.isFinite(bytes)) return "—";
	if (bytes < 1024) return `${bytes} B`;
	const KB = 1024;
	const MB = KB * 1024;
	const GB = MB * 1024;
	if (bytes < MB) return `${(bytes / KB).toFixed(1)} KB`;
	if (bytes < GB) return `${(bytes / MB).toFixed(1)} MB`;
	return `${(bytes / GB).toFixed(2)} GB`;
}

function formatSpeed(bps: number | null | undefined): string {
	if (bps == null || bps < 0 || !Number.isFinite(bps)) return "—";
	const KB = 1024;
	const MB = KB * 1024;
	const GB = MB * 1024;
	if (bps < KB) return `${bps.toFixed(0)} B/s`;
	if (bps < MB) return `${(bps / KB).toFixed(0)} KB/s`;
	if (bps < MB * 100) return `${(bps / MB).toFixed(1)} MB/s`;
	return `${(bps / GB).toFixed(2)} GB/s`;
}

function formatEta(seconds: number | null | undefined): string {
	if (seconds == null || seconds < 0 || !Number.isFinite(seconds)) return "—";
	const s = Math.floor(seconds % 60);
	const m = Math.floor((seconds / 60) % 60);
	const h = Math.floor(seconds / 3600);
	const pad = (n: number) => n.toString().padStart(2, "0");
	if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
	return `${pad(m)}:${pad(s)}`;
}

/**
 * XA-13 priority #4: builds the progressbar's aria-label. When
 * `modelName` is provided, SR users hear "{name} download: N% complete"
 * — disambiguating between concurrent downloads (e.g. Whisper + Parakeet
 * shown on the same Models page). Falls back to the generic
 * `models.download.progressAria` when no name is supplied (preserves
 * the pre-fix behaviour for callers that haven't been updated).
 */
function progressAriaLabel(progress: number, modelName?: string): string {
	const percent = String(Math.round(progress));
	if (modelName) {
		return t("models.download.progressAriaWithName", {
			name: modelName,
			percent,
		});
	}
	return t("models.download.progressAria", { percent });
}

export function DownloadProgressBar({
	progress,
	status,
	isPaused,
	downloadedBytes,
	totalBytes,
	speedBps,
	etaSeconds,
	onTogglePause,
	onCancel,
	modelName,
	error,
	onRetry,
}: DownloadProgressBarProps) {
	const hasError = Boolean(error);
	// When the download has failed, the bar fill turns red so users
	// instantly see the failure even without reading the status line.
	// Paused → amber (unchanged). Otherwise → accent.
	const fillClass = hasError
		? "bg-destructive"
		: isPaused
			? "bg-amber-500"
			: "bg-accent";

	// XA-13-M5: when in the error state, the status <p> becomes an
	// alert region. role="alert" is implicitly aria-live="assertive",
	// so SR users hear the failure announcement as soon as the error
	// prop is set — without needing to focus the bar.
	const statusText = hasError
		? modelName
			? t("models.download.errorMessageWithName", {
					name: modelName,
					error: error ?? "",
				})
			: t("models.download.errorMessage", { error: error ?? "" })
		: status;

	return (
		<div className="space-y-2">
			<div
				className="h-1.5 w-full rounded-full bg-border overflow-hidden"
				role="progressbar"
				aria-label={progressAriaLabel(progress, modelName)}
				aria-valuemin={0}
				aria-valuemax={100}
				// NF-R15-17: throttle aria-valuenow to the nearest 10% so screen
				// readers don't broadcast a stream of percentage updates every
				// frame (the visual bar still updates smoothly via the width
				// style below).
				aria-valuenow={Math.round(progress / 10) * 10}
			>
				<div
					className={`h-full rounded-full transition-all duration-300 ${fillClass}`}
					style={{ width: `${progress}%` }}
				/>
			</div>
			<div className="flex items-center justify-between gap-3">
				<p
					className={`flex-1 min-w-0 truncate text-xs ${
						hasError ? "text-destructive font-medium" : "text-(--text-muted)"
					}`}
					// XA-13-M5: in the error state this region becomes an
					// assertive live region so the failure is announced
					// automatically. In the normal state it remains a polite
					// live region (BG-75) so progress updates are announced
					// without interrupting the user.
					role={hasError ? "alert" : undefined}
					aria-live={hasError ? "assertive" : "polite"}
				>
					{statusText}
					{!hasError && isPaused && (
						// XA-13-M8: render the `models.progress.paused` chip
						// (catalog value: "· Paused"). The i18n key has existed
						// since PVT-003 but was never rendered — the only paused
						// cue was the amber bar fill, which is invisible to SR
						// users and easy to miss for sighted users.
						<span className="ms-2 whitespace-nowrap">
							{t("models.progress.paused")}
						</span>
					)}
					{!hasError && downloadedBytes !== null && totalBytes !== null && (
						<span className="ms-2 whitespace-nowrap">
							· {formatBytes(downloadedBytes)} / {formatBytes(totalBytes)}
						</span>
					)}
					{!hasError && speedBps !== null && speedBps > 0 && (
						<span className="ms-2 whitespace-nowrap">
							· {formatSpeed(speedBps)}
						</span>
					)}
					{!hasError && etaSeconds !== null && etaSeconds > 0 && (
						<span className="ms-2 whitespace-nowrap">
							·{" "}
							{t("models.progress.eta", {
								time: formatEta(etaSeconds),
							})}
						</span>
					)}
				</p>
				<div className="flex items-center gap-2 shrink-0">
					{hasError && onRetry && (
						// XA-13 priority #3: in-place retry. Without this
						// button the only recovery path is to re-navigate to
						// the model card and click Download again — which is
						// particularly painful for the Parakeet case (XA-13-C1)
						// where the user has just watched a multi-GB download
						// fail at 90%+.
						<Button
							variant="outline"
							size="sm"
							onClick={onRetry}
							aria-label={t("models.download.retryAria")}
							className="h-7 gap-1 px-3 text-xs"
						>
							{t("models.download.retry")}
						</Button>
					)}
					<Button
						variant="outline"
						size="sm"
						onClick={onTogglePause}
						// XA-13-M5: pausing a failed download is a no-op —
						// disable the affordance so users don't waste a click
						// expecting it to do something. We keep the button
						// visible (rather than hiding it) so the layout doesn't
						// shift and the existing Pause/Resume aria-label is
						// still discoverable.
						disabled={hasError}
						aria-label={
							isPaused
								? t("models.download.resumeAria")
								: t("models.download.pauseAria")
						}
						className="h-7 gap-1 px-3 text-xs"
					>
						<HugeiconsIcon
							icon={isPaused ? PlayIcon : PauseIcon}
							strokeWidth={2}
							className="h-3.5 w-3.5"
						/>
						{isPaused
							? t("models.download.resume")
							: t("models.download.pause")}
					</Button>
					<Button
						variant="outline"
						size="sm"
						onClick={onCancel}
						aria-label={t("models.download.cancelAria")}
						className="h-7 px-3 text-xs"
					>
						{t("models.download.cancel")}
					</Button>
				</div>
			</div>
		</div>
	);
}
