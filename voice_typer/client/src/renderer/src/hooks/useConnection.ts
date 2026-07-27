import { useCallback, useEffect } from "react";
import { usePythonEvent } from "@/hooks/usePython";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page, RecordingState } from "@/types/ipc";

// NEW-TS-012: runtime validator for the RecordingState string-literal
// union.  The backend emits status values as plain strings over IPC;
// previously we cast them to ``RecordingState`` without validation,
// which would silently propagate unknown values through the type
// system.  This validator returns ``null`` for unknown values so the
// caller can discard them instead of corrupting React state.
const RECORDING_STATES: ReadonlySet<string> = new Set([
	"idle",
	"recording",
	"transcribing",
	"loading",
	"cancelling",
	"error",
]);

function asRecordingState(value: unknown): RecordingState | null {
	if (typeof value !== "string") return null;
	return RECORDING_STATES.has(value) ? (value as RecordingState) : null;
}

interface UseConnectionArgs {
	/** Python bridge `call` function (from usePython). */
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>;
	/**
	 * Current page (historically used for onboarding first-run
	 * auto-routing check).
	 *
	 * @deprecated No longer read inside the hook (the first-run
	 * check is now unconditional). Kept for backward compatibility
	 * with existing callers (App.tsx); new callers should omit it.
	 *
	 * F1 (b-review Finding 5): the first-run check is now performed
	 * unconditionally on the initial connection probe regardless of
	 * the persisted page (see the effect body for rationale), so this
	 * field is no longer read inside the hook. It's kept in the
	 * interface for backward compatibility with existing callers
	 * (App.tsx) and to leave the door open for future page-aware
	 * routing logic.
	 */
	currentPage?: Page;
	/** Navigate callback (used to route to onboarding on first run). */
	navigate: (page: Page) => void;
}

/**
 * Connection hook: manages the lifecycle of the Python backend
 * connection, recording state pushed from the backend, and transient
 * TCP recovery (reconnecting/reconnected events).  Also exposes a
 * retry callback for the "Lost connection" UI.
 *
 * BACKLOG-004: State is now backed by the Zustand appStore so any
 * component can subscribe to connectionStatus / recordingState /
 * lastError without prop drilling through App.tsx. The hook retains
 * the lifecycle effects (connection probe, health check, event
 * subscriptions) and returns the same interface for backward compat.
 *
 * @param args.call         Python bridge call fn.
 * @param args.currentPage  Current page (for onboarding first-run auto-route).
 * @param args.navigate     Navigation fn (for onboarding first-run auto-route).
 */
export function useConnection({
	call,
	// F1: `currentPage` is no longer read inside the hook (the first-run
	// check is now unconditional). It's still part of the
	// UseConnectionArgs interface for backward compatibility with
	// App.tsx, so we accept + discard it here. The leading underscore
	// tells biome's noUnusedFunctionParameters rule this is intentional.
	currentPage: _currentPage,
	navigate,
}: UseConnectionArgs) {
	// ── Store-backed state ────────────────────────────────────────
	const setConnectionStatus = useAppStore((s) => s.setConnectionStatus);
	const setRecordingState = useAppStore((s) => s.setRecordingState);
	const setLastError = useAppStore((s) => s.setLastError);
	const setConfig = useAppStore((s) => s.setConfig);
	const connectionStatus = useAppStore((s) => s.connectionStatus);
	const recordingState = useAppStore((s) => s.recordingState);
	const lastError = useAppStore((s) => s.lastError);

	// ── Connection lifecycle ──────────────────────────────────────

	useEffect(() => {
		let retries = 0;
		const maxRetries = 5;
		let timer: ReturnType<typeof setTimeout>;
		let cancelled = false;

		const checkConnection = async () => {
			if (cancelled) return;
			try {
				const cfg = await call<VoiceTyperConfig>("get_config");
				if (!cancelled) {
					setConnectionStatus("connected");
					// Cache the config snapshot in the store so other
					// components (e.g. Settings sections) can read it
					// without an extra IPC round-trip.
					setConfig(cfg);
					// Sync current state from backend (status_change events sent before
					// the React app mounted are lost — this ensures we catch up)
					call<{ status: string }>("get_status")
						.then((s) => {
							if (!cancelled && s?.status) {
								const validated = asRecordingState(s.status);
								if (validated) setRecordingState(validated);
							}
						})
						// G4-H-22: surface get_status failures to the
						// renderer console instead of silently swallowing
						// them so a hung backend probe is observable in
						// the Electron main-process log.
						.catch((err) => console.warn("[IPC] get_status failed:", err));
					// Send saved bubble_position to the Electron main process
					// so it persists across restarts (main process initializes to 'top')
					const pos = cfg?.bubble_position;
					if (pos === "bottom" || pos === "top") {
						window.bubble?.setPosition?.(pos);
					}
					// Sync saved bubble_draggable state so the main process has the
					// correct value before the bubble is ever shown
					const draggable = cfg?.bubble_draggable;
					if (typeof draggable === "boolean") {
						window.bubble?.setDraggable?.(draggable);
					}
					// Show the bubble at startup if always_visible + show_on_startup is enabled.
					// This is a reliable fallback in case the TCP push event from Python's
					// _do_startup arrives before Electron is fully ready to render the bubble.
					const behavior = cfg?.bubble_behavior;
					const showOnStartup = cfg?.bubble_show_on_startup;
					if (behavior === "always_visible" && showOnStartup !== false) {
						window.bubble?.show?.();
					}

					// #8: Onboarding wizard — detect first run and route the user
					// to the wizard. The backend's `onboarding_is_first_run` IPC route
					// checks config.onboarding_completed (and the marker file).
					//
					// F1 (b-review Finding 5): previously this was gated on
					// `currentPage === "home"`, but useNavigation restores the
					// persisted page from localStorage on mount — so a user who
					// closed the app mid-onboarding while on "settings" (or any
					// non-home page) would land back on that page on next launch
					// and the wizard would be silently skipped. We now check
					// first-run unconditionally on the initial connection probe;
					// if `is_first_run` is true, we force-navigate to onboarding
					// regardless of the persisted page. The persisted nav state
					// (history + index) is still there after the wizard completes,
					// so back/forward navigation continues to work.
					if (!cancelled) {
						try {
							const fr = await call<{ is_first_run: boolean }>(
								"onboarding_is_first_run",
							);
							if (!cancelled && fr?.is_first_run) {
								navigate("onboarding");
							}
						} catch (e) {
							// Older backend without the IPC route — silently ignore.
							console.warn(
								"[useConnection] onboarding_is_first_run probe failed:",
								e,
							);
						}
					}
				}
			} catch {
				retries++;
				if (!cancelled && retries < maxRetries) {
					timer = setTimeout(checkConnection, 2000);
				} else if (!cancelled) {
					setConnectionStatus("disconnected");
				}
			}
		};

		checkConnection();

		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	}, [call, navigate, setConnectionStatus, setRecordingState, setConfig]);

	// Periodic health check while connected
	// PR-1: use ``get_status`` (lightweight — returns only state +
	// xrun counter) instead of ``get_config`` (serializes the entire
	// config dict).  The RW-10 heartbeat (5s backend→frontend check)
	// detects Electron crashes; this renderer→backend check detects
	// backend crashes.  Together they provide bidirectional crash
	// detection without config serialization churn.
	//
	// PVT-fix-16: the previous implementation flipped to
	// ``"disconnected"`` after a SINGLE failed ``get_status`` call.
	// A transient TCP hiccup (GC pause, OS scheduler delay, brief
	// socket congestion) would mark the backend dead even though the
	// process was still alive — the user saw a jarring "Lost
	// connection" UI and had to click Retry. We now retry up to
	// ``HEALTH_CHECK_MAX_RETRIES`` times with a short backoff before
	// declaring the backend disconnected. A success at any retry
	// tier resets the failure counter and the cycle continues.
	//
	// BG-92: the previous 60s interval was too coarse — a dead
	// backend could sit undetected for up to a minute before the
	// user saw any feedback (the RW-10 backend→frontend heartbeat
	// only catches ELECTRON crashes, not Python-side crashes). The
	// 15s interval catches a dead backend within ~15s of the last
	// successful probe, which is the threshold at which users
	// perceive "the app is hung" and start clicking around. The
	// 2-strike retry (``HEALTH_CHECK_MAX_RETRIES = 2``) still
	// tolerates transient flaps; the steady-state IPC load is one
	// ``get_status`` call every 15s (trivial — the response is a
	// 30-byte JSON envelope).
	useEffect(() => {
		if (connectionStatus !== "connected") return;

		let cancelled = false;
		// Number of quick retries before declaring disconnected.
		// 3 strikes (initial attempt + 2 retries) ≈ 1s of total
		// tolerance for a transient flap before we surface the
		// outage to the user.
		const HEALTH_CHECK_MAX_RETRIES = 2;
		const HEALTH_CHECK_RETRY_DELAY_MS = 500;
		let retryTimer: ReturnType<typeof setTimeout> | undefined;
		let failureCount = 0;

		const probe = async (isRetry: boolean): Promise<void> => {
			try {
				await call("get_status");
				failureCount = 0;
			} catch {
				if (cancelled) return;
				if (isRetry) failureCount++;
				else failureCount = 1;
				if (failureCount > HEALTH_CHECK_MAX_RETRIES) {
					setConnectionStatus("disconnected");
				} else {
					// Schedule a quick retry so a transient flap
					// doesn't have to wait for the next 15s tick.
					retryTimer = setTimeout(() => {
						probe(true);
					}, HEALTH_CHECK_RETRY_DELAY_MS);
				}
			}
		};

		const interval = setInterval(() => probe(false), 15_000);

		return () => {
			cancelled = true;
			clearInterval(interval);
			if (retryTimer) clearTimeout(retryTimer);
		};
	}, [connectionStatus, call, setConnectionStatus]);

	// ── Background reconnect poll ─────────────────────────
	//
	// While `connectionStatus === "disconnected"`, the user sees the
	// "Lost connection" screen (ConnectionStatusScreen) and the only
	// recovery path is the manual Retry button — there's no
	// auto-recovery from a transient backend outage (e.g. backend
	// restarted while the renderer was idle). This slow background
	// poll attempts a single `get_config` every 10s while
	// disconnected. If the probe succeeds, we flip to "connected"
	// (which re-runs the connection lifecycle effect above for the
	// normal config + onboarding probe path) and stop polling. If
	// the probe fails, we keep polling up to MAX_BACKGROUND_RECONNECTS
	// attempts (12 × 10s = 2 minutes) so a truly-dead backend
	// doesn't spin an interval forever. The cap is generous enough
	// to recover from a 60–90s backend restart cycle but bounded
	// enough to release the timer closure before it leaks.
	//
	// Re-arm: any transition OUT of "disconnected" (manual Retry
	// click → "connecting" → success → "connected") re-mounts this
	// effect (dep on `connectionStatus`) and resets `attempts`.
	// Likewise, a transition back INTO "disconnected" starts a fresh
	// 12-attempt budget. The cap exists to prevent infinite polling
	// against a backend that's never coming back; the user can still
	// hit Retry manually at any time.
	useEffect(() => {
		if (connectionStatus !== "disconnected") return;

		let cancelled = false;
		let attempts = 0;
		let timer: ReturnType<typeof setTimeout> | undefined;
		const MAX_BACKGROUND_RECONNECTS = 12; // 12 × 10s = 2 minutes
		const BACKGROUND_RECONNECT_INTERVAL_MS = 10_000;

		const tryReconnect = async () => {
			if (cancelled) return;
			if (attempts >= MAX_BACKGROUND_RECONNECTS) return;
			attempts++;
			try {
				await call<VoiceTyperConfig>("get_config");
				if (!cancelled) {
					// Success — flip to "connected". The
					// connection-lifecycle effect above
					// doesn't re-run on this transition
					// (its deps don't include
					// connectionStatus), so we also pull
					// config + recording state here so the
					// app doesn't show stale data while
					// waiting for the next status_change
					// push.
					setConnectionStatus("connected");
					setLastError(null);
					call<{ status: string }>("get_status")
						.then((s) => {
							if (!cancelled && s?.status) {
								const validated = asRecordingState(s.status);
								if (validated) setRecordingState(validated);
							}
						})
						.catch((err) =>
							console.warn(
								"[useConnection] background-reconnect get_status failed:",
								err,
							),
						);
				}
			} catch {
				if (!cancelled && attempts < MAX_BACKGROUND_RECONNECTS) {
					timer = setTimeout(tryReconnect, BACKGROUND_RECONNECT_INTERVAL_MS);
				}
				// On the final attempt we just stop — the
				// user can click Retry to start another
				// 12-attempt cycle.
			}
		};

		// Delay the first probe by the full interval so a brief
		// disconnect (e.g. backend restarting) doesn't immediately
		// hammer it with a redundant `get_config` call (the
		// connection-lifecycle effect's 5 quick retries already
		// cover the first ~10s of outages).
		timer = setTimeout(tryReconnect, BACKGROUND_RECONNECT_INTERVAL_MS);

		return () => {
			cancelled = true;
			if (timer) clearTimeout(timer);
		};
	}, [
		connectionStatus,
		call,
		setConnectionStatus,
		setLastError,
		setRecordingState,
	]);

	// ── App-level event subscriptions ─────────────────────────────

	usePythonEvent(
		"status_change",
		useCallback(
			(data): (() => void) | undefined => {
				if (data?.status) {
					const validated = asRecordingState(data.status);
					if (validated) {
						setRecordingState(validated);
						setLastError(null);
					}
				}
				return undefined;
			},
			[setRecordingState, setLastError],
		),
	);

	usePythonEvent(
		"error",
		useCallback(
			(data): (() => void) | undefined => {
				if (typeof data?.message === "string") {
					setLastError(data.message);
				}
				return undefined;
			},
			[setLastError],
		),
	);

	// ── Transient TCP recovery ───────────────────────────────────
	usePythonEvent(
		"reconnecting",
		useCallback((): (() => void) | undefined => {
			// UX-22: `"restarting"` is a member of the ConnectionStatus
			// union (see appStore.ts), so the `as ConnectionStatus` cast
			// was redundant dead code.
			setConnectionStatus("restarting");
			return undefined;
		}, [setConnectionStatus]),
	);
	usePythonEvent(
		"reconnected",
		useCallback((): (() => void) | undefined => {
			call("get_config")
				.then(() => setConnectionStatus("connected"))
				.catch(() => setConnectionStatus("disconnected"));
			return undefined;
		}, [call, setConnectionStatus]),
	);

	// PVT-G5-060: the backend emits `state_changed` once on every
	// client connect (see `voice_typer/server/ipc_server.py:1311-1326`)
	// carrying the connect-time snapshot of the app state —
	// `{ status: AppState.value, message: str }`.  Without a
	// subscriber, this snapshot was silently dropped and the
	// renderer had to discover the current recording state via a
	// separate `get_status` round-trip (or wait for the next
	// `status_change` transition, which could be indefinitely far
	// away if the backend was idle).
	//
	// We treat `state_changed` as a stronger signal than
	// `status_change`: a push from the backend means the connection
	// is healthy, so we optimistically flip `connectionStatus` to
	// "connected" and clear any stale `lastError`. The recording
	// state is updated from `data.status` when it validates against
	// the RecordingState union; unknown / missing values are
	// discarded (defensive — the backend may add new states before
	// the renderer ships a matching type).
	usePythonEvent(
		"state_changed",
		useCallback(
			(data): (() => void) | undefined => {
				// A push from the backend means we have a live
				// connection — surface that immediately.
				setConnectionStatus("connected");
				setLastError(null);

				const rawStatus = data?.status;
				if (typeof rawStatus === "string") {
					const validated = asRecordingState(rawStatus);
					if (validated) {
						setRecordingState(validated);
					}
					// If `validated` is null the backend sent a
					// state we don't recognise — leave the
					// existing recordingState untouched so we
					// don't clobber a valid state with garbage.
				}
				return undefined;
			},
			[setConnectionStatus, setLastError, setRecordingState],
		),
	);

	// ── Reconnection handler (called by children on fatal errors) ─

	const handleRetryConnection = useCallback(async () => {
		setConnectionStatus("connecting");
		try {
			await call("get_config");
			setConnectionStatus("connected");
		} catch {
			setConnectionStatus("disconnected");
		}
	}, [call, setConnectionStatus]);

	return {
		recordingState,
		connectionStatus,
		lastError,
		handleRetryConnection,
	};
}
