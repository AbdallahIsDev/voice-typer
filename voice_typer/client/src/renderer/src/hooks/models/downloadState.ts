export interface FailedDownload {
	modelName: string;
	error: string;
}

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
