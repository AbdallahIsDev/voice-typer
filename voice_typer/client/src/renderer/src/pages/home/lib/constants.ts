//shared constants extracted from Home.tsx so the
// main page file stays a thin composition root. Pure values — no
// behaviour, no React, no IPC.

/**
 * localStorage key for the recent-activity cache (last 5 transcriptions).
 * Used by `loadCachedRecent` / `persistRecent` in `./cache.ts`.
 */
export const RECENT_CACHE_KEY = "vt_home_recent_cache";

/**
 * localStorage key for the today-stats cache (`get_today_stats` payload).
 * Used by `loadCachedStats` / `persistStats` in `./cache.ts`.
 */
export const STATS_CACHE_KEY = "vt_home_stats_cache";

/**
 * localStorage flag (value `"1"`) set the first time the user completes a
 * transcription, so the celebratory toast only fires once per install.
 * Read by `useFirstRecordingCelebration` in `../hooks/`.
 */
export const FIRST_RECORD_CELEBRATED_KEY = "vt_first_recording_celebrated";

/**
 * After this many milliseconds in the "transcribing" state, the Home page
 * reveals a "Force cancel" affordance so the user can abort a stuck
 * transcription. Lowered from 60s → 5s — a genuinely stuck transcription
 * is obvious within seconds; 60s of silence is far too patient.
 */
export const FORCE_CANCEL_DELAY_MS = 5_000;

/**
 * The last-transcription preview card auto-clears after this many
 * milliseconds of idle so the previous transcription isn't exposed on a
 * shared/locked screen.
 */
export const LAST_TEXT_AUTO_CLEAR_MS = 30_000;

/**
 * Status-key → CSS color mapping for the Home status pill dot,
 * aligned with `voice_typer/server/tray_icon.py` color-blind-safe
 * palette so the pill matches the tray icon semantics. The key is the
 * lowercase status string emitted by the backend's `status_change`
 * event / `RecordingState` enum.
 *
 * Values are theme CSS variables (NOT raw hex) so the dot adapts to
 * every theme preset and custom palette — the same tokens every other
 * surface uses (`--success` / `--warning` / `--info` are defined by
 * index.css for both light and dark and backfilled by all presets +
 * the custom-theme generator; see themes/__tests__/status-tokens.test.ts).
 * Rendered via inline `backgroundColor`, which resolves the var at
 * paint time.
 */
export const STATUS_COLORS: Record<string, string> = {
	idle: "var(--text-muted)",
	recording: "var(--success)",
	transcribing: "var(--info)",
	loading: "var(--warning)",
	cancelling: "var(--warning)",
	error: "var(--destructive)",
};

/**
 * Fallback dot color for a status key missing from `STATUS_COLORS`
 * (matches `STATUS_COLORS.idle`). Exported so Home's lookup fallback
 * references one authoritative value instead of re-inlining a literal.
 */
export const DEFAULT_STATUS_COLOR = "var(--text-muted)";
