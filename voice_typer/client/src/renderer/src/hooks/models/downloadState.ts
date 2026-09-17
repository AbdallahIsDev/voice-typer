export interface FailedDownload {
	modelName: string;
	error: string;
}

/**
 * Consolidated download-progress state. Previously 9 separate `useState`
 * fields; each `download_progress` event now produces ONE setState via a
 * `Partial<DownloadState>` patch containing only the fields present in
 * the event payload (see `buildDownloadProgressPatch`).
 */
export interface DownloadState {
	downloadingModel: string | null;
	downloadProgress: number;
	downloadStatus: string;
	isPaused: boolean;
	downloadedBytes: number | null;
	totalBytes: number | null;
	speedBps: number | null;
	etaSeconds: number | null;
	failedDownload: FailedDownload | null;
}

export const INITIAL_DOWNLOAD_STATE: DownloadState = {
	downloadingModel: null,
	downloadProgress: 0,
	downloadStatus: "",
	isPaused: false,
	downloadedBytes: null,
	totalBytes: null,
	speedBps: null,
	etaSeconds: null,
	failedDownload: null,
};

/**
 * Zero the progress-related fields (preserving `downloadingModel`,
 * `failedDownload`). Pure so the claim-time updater inside
 * `downloadModel` can reuse it, an updater must not call `setState`
 * (which the `resetProgress` callback does). This is the SAME field set
 * `resetProgress` clears, kept in one place.
 */
export function withResetProgress(prev: DownloadState): DownloadState {
	return {
		...prev,
		downloadProgress: 0,
		downloadStatus: "",
		downloadedBytes: null,
		totalBytes: null,
		speedBps: null,
		etaSeconds: null,
		isPaused: false,
	};
}

/**
 * Build the state patch for one `download_progress` event payload.
 * Only fields present in the event are set, others are preserved via
 * the `{ ...prev, ...patch }` spread at the call site.
 *
 * Speed/ETA: set when present; cleared ONLY on a state transition
 * (pause/resume/status change), the backend also pushes
 * transition-only events (e.g. a lone `paused: true`) whose absent
 * speed/ETA fields mean "not re-measured", not "reset to zero".
 * Clearing on absence made every partial event wipe the live
 * speed/ETA readout.
 */
export function buildDownloadProgressPatch(
	data: Record<string, unknown> | undefined,
): Partial<DownloadState> | null {
	if (!data) return null;
	const patch: Partial<DownloadState> = {};
	if (typeof data.progress === "number") patch.downloadProgress = data.progress;
	if (typeof data.status === "string") patch.downloadStatus = data.status;
	if (typeof data.downloaded_bytes === "number")
		patch.downloadedBytes = data.downloaded_bytes;
	if (typeof data.total_bytes === "number") patch.totalBytes = data.total_bytes;
	const isTransition =
		typeof data.status === "string" ||
		typeof data.paused === "boolean" ||
		data.resumed === true;
	if (typeof data.speed_bytes_per_sec === "number") {
		patch.speedBps = data.speed_bytes_per_sec;
	} else if (data.speed_bytes_per_sec == null && isTransition) {
		patch.speedBps = null;
	}
	if (typeof data.eta_seconds === "number") {
		patch.etaSeconds = data.eta_seconds;
	} else if (data.eta_seconds == null && isTransition) {
		patch.etaSeconds = null;
	}
	if (typeof data.paused === "boolean") patch.isPaused = data.paused;
	if (typeof data.resumed === "boolean" && data.resumed) patch.isPaused = false;
	return Object.keys(patch).length > 0 ? patch : null;
}

/**
 * Apply a patch with the original per-`useState` bailout semantics.
 * The consolidated form creates a new state object on every call,
 * which would defeat React's `Object.is` bailout, so each patched
 * field is compared against `prev` and `prev` (same reference) is
 * returned when nothing changed. React's `Object.is` check then skips
 * the re-render, matching the original behaviour.
 */
export function applyDownloadPatch(
	prev: DownloadState,
	patch: Partial<DownloadState>,
): DownloadState {
	let changed = false;
	for (const key of Object.keys(patch) as (keyof DownloadState)[]) {
		if (!Object.is(prev[key], (patch as DownloadState)[key])) {
			changed = true;
			break;
		}
	}
	return changed ? { ...prev, ...patch } : prev;
}
