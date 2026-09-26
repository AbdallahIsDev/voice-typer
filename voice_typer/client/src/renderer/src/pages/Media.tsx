// Media page (ADR-0023): paste a link or drop a local file, opt into the
// subtitle fast-path, then watch the phased progress (downloading /
// loading model / transcribing) with a measured ETA. Job lifecycle lives
// in `media/hooks/useMediaJob`; this file is the view.

import {
	AlertCircleIcon,
	CheckmarkCircle01Icon,
	HistoryIcon,
	PlayCircleIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import { Spinner } from "@/components/feedback/Spinner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useNavigation } from "@/hooks/useNavigation";
import { useT } from "@/i18n/i18n";
import { formatClockDuration } from "@/lib/format";
import { useMediaJob } from "./media/hooks/useMediaJob";
import { isNoModelError } from "./media/lib/mediaErrorCopy";

/** ADR-0023 E11: past this length the page warns about the total time. */
const LONG_MEDIA_SECONDS = 4 * 3600;

export default function MediaPage() {
	const t = useT();
	const { navigate } = useNavigation();
	const { job, starting, running, start, cancel } = useMediaJob();
	const [source, setSource] = useState("");
	const [useSubtitles, setUseSubtitles] = useState(false);

	// Native file drop supplies ABSOLUTE paths (the web platform hides
	// them); `window_.onDragDropFiles` is a no-op outside Tauri.
	useEffect(() => {
		const api = window.window_;
		if (!api?.onDragDropFiles) return;
		return api.onDragDropFiles((paths) => {
			if (Array.isArray(paths) && paths.length > 0 && paths[0]) {
				setSource(paths[0]);
			}
		});
	}, []);

	const trimmed = source.trim();
	const canStart = trimmed.length > 0 && !running && !starting;
	const percent = Math.max(0, Math.min(100, Math.round(job.progress * 100)));
	const phaseLabel =
		job.phase === "downloading"
			? t("media.phaseDownloading")
			: job.phase === "transcribing"
				? t("media.phaseTranscribing")
				: t("media.phaseLoadingModel");
	const isLongMedia =
		job.durationSeconds !== null && job.durationSeconds > LONG_MEDIA_SECONDS;

	const handleStart = () => {
		if (!canStart) return;
		void start(trimmed, useSubtitles);
	};

	return (
		<div className="flex flex-col gap-6">
			<PageHeading
				title={t("media.title")}
				description={t("media.description")}
			/>

			<div className="flex flex-col gap-4 rounded-lg border border-border/10 bg-surface-subtle p-4">
				<label className="flex flex-col gap-2" htmlFor="media-source">
					<span className="text-sm font-medium text-foreground">
						{t("media.sourceLabel")}
					</span>
					<Input
						id="media-source"
						value={source}
						onChange={(e) => setSource(e.target.value)}
						placeholder={t("media.sourcePlaceholder")}
						disabled={running}
						onKeyDown={(e) => {
							if (e.key === "Enter") handleStart();
						}}
					/>
					<span className="text-xs text-muted-foreground">
						{t("media.dropHint")}
					</span>
				</label>

				<div className="flex items-center justify-between gap-3">
					<div className="flex min-w-0 flex-col gap-1">
						<span className="text-sm font-medium text-foreground">
							{t("media.useSubtitlesLabel")}
						</span>
						<span className="text-xs text-muted-foreground">
							{t("media.useSubtitlesInfo")}
						</span>
					</div>
					<Switch
						checked={useSubtitles}
						onCheckedChange={setUseSubtitles}
						disabled={running}
						aria-label={t("media.useSubtitlesLabel")}
					/>
				</div>

				<div className="flex items-center gap-2">
					<Button onClick={handleStart} disabled={!canStart}>
						{starting ? (
							<Spinner className="border-current" decorative />
						) : (
							<HugeiconsIcon
								icon={PlayCircleIcon}
								strokeWidth={2}
								className="h-4 w-4"
							/>
						)}
						{t("media.start")}
					</Button>
					{running && (
						<Button variant="outline" onClick={() => void cancel()}>
							{t("media.cancel")}
						</Button>
					)}
				</div>
			</div>

			{running && (
				<div
					className="flex flex-col gap-3 rounded-lg border border-border/10 bg-surface-subtle p-4"
					role="status"
					aria-live="polite"
				>
					<div className="flex flex-wrap items-baseline justify-between gap-2">
						<span className="text-sm font-medium text-foreground">
							{phaseLabel}
						</span>
						<span className="text-xs text-muted-foreground tabular-nums">
							{t("media.percent", { percent: String(percent) })}
							{job.etaSeconds !== null &&
								` · ${t("media.eta", {
									time: formatClockDuration(job.etaSeconds),
								})}`}
							{job.durationSeconds !== null &&
								` · ${t("media.duration", {
									time: formatClockDuration(job.durationSeconds),
								})}`}
						</span>
					</div>
					<div
						className="h-1.5 w-full overflow-hidden rounded-full bg-border"
						role="progressbar"
						aria-valuemin={0}
						aria-valuemax={100}
						aria-valuenow={percent}
						aria-label={t("media.percent", { percent: String(percent) })}
					>
						<div
							className="h-full rounded-full bg-primary transition-transform duration-300"
							style={{ width: `${percent}%` }}
						/>
					</div>
					{isLongMedia && (
						<p className="text-xs text-muted-foreground">
							{t("media.longMediaNotice")}
						</p>
					)}
				</div>
			)}

			{job.phase === "done" && (
				<div
					className="flex flex-col gap-3 rounded-lg border border-border/10 bg-surface-subtle p-4"
					role="status"
				>
					<div className="flex items-center gap-2">
						<HugeiconsIcon
							icon={CheckmarkCircle01Icon}
							strokeWidth={2}
							className="h-4 w-4 text-primary"
						/>
						<span className="text-sm font-medium text-foreground">
							{t("media.completeTitle")}
						</span>
					</div>
					<p className="text-sm text-muted-foreground">
						{job.partial
							? t("media.partialDescription", {
									chars: String(job.chars ?? 0),
								})
							: t("media.completeDescription", {
									chars: String(job.chars ?? 0),
								})}
					</p>
					{job.rowId !== null && (
						<div>
							<Button
								variant="outline"
								size="sm"
								onClick={() => navigate("history")}
							>
								<HugeiconsIcon
									icon={HistoryIcon}
									strokeWidth={2}
									className="h-4 w-4"
								/>
								{t("media.openHistory")}
							</Button>
						</div>
					)}
				</div>
			)}

			{job.phase === "error" && (
				<div
					className="flex flex-col gap-3 rounded-lg border border-border/10 bg-surface-subtle p-4"
					role="alert"
				>
					<div className="flex items-center gap-2">
						<HugeiconsIcon
							icon={AlertCircleIcon}
							strokeWidth={2}
							className="h-4 w-4 text-destructive"
						/>
						<span className="text-sm font-medium text-foreground">
							{t("media.errorTitle")}
						</span>
					</div>
					<p className="text-sm text-muted-foreground">
						{job.errorKey ? t(job.errorKey) : t("media.errorGeneric")}
					</p>
					{isNoModelError(job.errorCode) && (
						<div>
							<Button
								variant="outline"
								size="sm"
								onClick={() => navigate("models")}
							>
								{t("media.noModelAction")}
							</Button>
						</div>
					)}
				</div>
			)}
		</div>
	);
}
