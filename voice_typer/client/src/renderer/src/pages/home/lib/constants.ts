// main page file stays a thin composition root. Pure values, no
// behaviour, no React, no IPC.

/**
 * localStorage key for the recent-activity cache (last 5 transcriptions).
 * Used by `loadCachedRecent` / `persistRecent` in `./cache.ts`.
 */
export const RECENT_CACHE_KEY = "vt_home_recent_cache";

export const STATS_CACHE_KEY = "vt_home_stats_cache";

export const FIRST_RECORD_CELEBRATED_KEY = "vt_first_recording_celebrated";

export const FORCE_CANCEL_DELAY_MS = 5_000;

export const STATUS_COLORS: Record<string, string> = {
	idle: "var(--muted-foreground)",
	recording: "var(--success)",
	transcribing: "var(--info)",
	loading: "var(--warning)",
	cancelling: "var(--warning)",
	error: "var(--destructive)",
};

export const DEFAULT_STATUS_COLOR = "var(--muted-foreground)";
