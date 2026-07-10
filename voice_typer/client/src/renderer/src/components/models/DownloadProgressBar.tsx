/**
 * DownloadProgressBar — standalone progress display for model downloads.
 *
 * Shows a progress bar with rich status info (downloaded/total, speed, ETA),
 * plus Pause/Resume and Cancel controls. Extracted from Models.tsx so it
 * can be placed independently of the model list.
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
}: DownloadProgressBarProps) {
	return (
		<div className="space-y-2">
			<div className="h-1.5 w-full rounded-full bg-border overflow-hidden">
				<div
					className={`h-full rounded-full transition-all duration-300 ${
						isPaused ? "bg-amber-500" : "bg-accent"
					}`}
					style={{ width: `${progress}%` }}
				/>
			</div>
			<div className="flex items-center justify-between gap-3">
				<p className="text-xs text-(--text-muted) flex-1 min-w-0 truncate">
					{status}
					{downloadedBytes !== null && totalBytes !== null && (
						<span className="ml-2 whitespace-nowrap">
							· {formatBytes(downloadedBytes)} / {formatBytes(totalBytes)}
						</span>
					)}
					{speedBps !== null && speedBps > 0 && (
						<span className="ml-2 whitespace-nowrap">
							· {formatSpeed(speedBps)}
						</span>
					)}
					{etaSeconds !== null && etaSeconds > 0 && (
						<span className="ml-2 whitespace-nowrap">
							· {t("models.progress.eta", { time: formatEta(etaSeconds) })}
						</span>
					)}
				</p>
				<div className="flex items-center gap-2 shrink-0">
					<Button
						variant="outline"
						size="sm"
						onClick={onTogglePause}
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
