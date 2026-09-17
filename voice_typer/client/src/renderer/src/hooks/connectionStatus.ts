import type { RecordingState } from "@/types/ipc";

/**
 * Runtime validator for the RecordingState string-literal
 * union.  The backend emits status values as plain strings over IPC;
 * previously we cast them to ``RecordingState`` without validation,
 * which would silently propagate unknown values through the type
 * system.  This validator returns ``null`` for unknown values so the
 * caller can discard them instead of corrupting React state.
 */
const RECORDING_STATES: ReadonlySet<string> = new Set([
	"idle",
	"recording",
	"transcribing",
	"loading",
	"cancelling",
	"error",
]);

export function asRecordingState(value: unknown): RecordingState | null {
	if (typeof value !== "string") return null;
	return RECORDING_STATES.has(value) ? (value as RecordingState) : null;
}

/**
 * Apply ONE backend status snapshot, the `{status, message}` tuple —
 * atomically to the store.
 *
 * SOURCE-OF-TRUTH INVARIANT: `recordingState` (drives the Home page's
 * "ERROR" status pill) and `lastError` (drives the red description line
 * below the mic) are two views of the SAME authoritative pair emitted by
 * the backend's tray state (`set_state(state, message)`). Every renderer
 * sync path MUST feed both fields from the same payload via this helper:
 * the live `status_change` push, the connect-time `state_changed`
 * snapshot, and both `get_status` catch-ups (the server includes
 * `message` in its `get_status` response for exactly this reason).
 *
 * REGRESSION TO AVOID: a sync path that sets `recordingState` without
 * deriving `lastError` from the same payload's message leaves the pill
 * stuck on ERROR while the description still shows the normal
 * "Press <hotkey> or click to dictate" hint, the intermittent mismatch
 * this helper exists to prevent. Conversely, non-error transitions clear
 * `lastError` so recovery restores the normal dictate guidance.
 */
export function applyStatusWithReason(
	status: RecordingState,
	message: string | null | undefined,
	setRecordingState: (s: RecordingState) => void,
	setLastError: (error: string | null) => void,
): void {
	setRecordingState(status);
	setLastError(status === "error" && message ? message : null);
}

/**
 * Structured code the supervisor's "respawn exhausted" condition is
 * signaled with on the `error` event (previously a brittle sentinel
 * substring match). When present, the handler flips `connectionStatus`
 * to `"disconnected"` in addition to setting a localized `lastError` —
 * otherwise the UI stays stuck on the transient `"restarting"` banner
 * forever.
 */
export const RESPAWN_EXHAUSTED_CODE = "respawn_exhausted";

/** Initial connection probe: attempts before declaring disconnected. */
export const CONNECTION_PROBE_MAX_RETRIES = 5;
/** Initial connection probe: delay between attempts. */
export const CONNECTION_PROBE_RETRY_DELAY_MS = 2000;

/**
 * Periodic health-check: quick retries before declaring disconnected.
 * 3 strikes (initial attempt + 2 retries) ≈ 1s of total tolerance for
 * a transient flap before the outage is surfaced to the user.
 */
export const HEALTH_CHECK_MAX_RETRIES = 2;
/** Periodic health-check: delay before a quick retry. */
export const HEALTH_CHECK_RETRY_DELAY_MS = 500;
/**
 * Periodic health-check: skip the active probe if a push event was
 * received within this grace window. 60s matches the "user perceives
 * hung" threshold, a backend that hasn't pushed for 60s is either dead
 * or stuck, and the active probe is the right tool to disambiguate.
 */
export const HEALTH_CHECK_EVENT_GRACE_MS = 60_000;
/**
 * Periodic health-check: steady-state interval. Catches a dead backend
 * within ~15s of the last successful probe; the 2-strike retry still
 * tolerates transient flaps. Steady-state IPC load is one lightweight
 * `get_status` call per tick (the response is a ~30-byte envelope).
 */
export const HEALTH_CHECK_INTERVAL_MS = 15_000;

/**
 * Background reconnect poll: max attempts while disconnected
 * (12 × 10s = 2 minutes). Generous enough to recover from a 60–90s
 * backend restart cycle but bounded enough to release the timer
 * closure before it leaks.
 */
export const MAX_BACKGROUND_RECONNECTS = 12;
/** Background reconnect poll: delay between attempts. */
export const BACKGROUND_RECONNECT_INTERVAL_MS = 10_000;
