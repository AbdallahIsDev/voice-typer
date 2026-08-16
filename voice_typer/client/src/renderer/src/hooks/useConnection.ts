import { useCallback, useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import { usePythonEvent } from "@/hooks/usePython";
import { useT } from "@/i18n/i18n";
import { useAppStore } from "@/stores/appStore";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page, RecordingState } from "@/types/ipc";

//runtime validator for the RecordingState string-literal
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
	const t = useT();
	// Consolidated selectors: previously this hook made 7 separate
	// `useAppStore` calls (4 actions + 3 values). Zustand runs every
	// registered selector on every `set()` call, so each additional
	// selector adds a small per-state-change cost. `useShallow`
	// collapses the 4 stable action references into a single
	// subscription (the shallow-equal return object only changes when
	// one of the actions changes identity — which never happens for
	// zustand store actions — so this hook does NOT re-render on
	// unrelated state changes). The 3 value selectors stay as
	// individual `useAppStore` calls so each one only re-renders when
	// its specific slice changes (e.g. `recordingState` changing from
	// "idle" to "recording" does NOT re-render this hook's subscribers
	// for `connectionStatus`).
	const { setConnectionStatus, setRecordingState, setLastError, setConfig } =
		useAppStore(
			useShallow((s) => ({
				setConnectionStatus: s.setConnectionStatus,
				setRecordingState: s.setRecordingState,
				setLastError: s.setLastError,
				setConfig: s.setConfig,
			})),
		);
	const connectionStatus = useAppStore((s) => s.connectionStatus);
	const recordingState = useAppStore((s) => s.recordingState);
	const lastError = useAppStore((s) => s.lastError);

	//timestamp of the last push event received from the Python
	// backend (any type — state_changed, status_change, bubble_level,
	// etc.). Updated by every `usePythonEvent` subscriber below via the
	// shared `_markEventReceived` callback. Used by the periodic health
	// check to skip the redundant `get_status` probe when a push event
	//has landed recently (the backend's  heartbeat + push events
	// already prove liveness; the 15s poll is belt-and-suspenders for
	// the case where pushes stop entirely). Stored in a ref (not state)
	// because it doesn't need to trigger a re-render — only the
	// periodic-probe effect reads it.
	const lastEventReceivedAtRef = useRef<number>(0);
	const markEventReceived = useCallback(() => {
		lastEventReceivedAtRef.current = Date.now();
	}, []);

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
						//surface get_status failures to the
						// renderer console instead of silently swallowing
						// them so a hung backend probe is observable in
						// the Electron main-process log.
						.catch((err) =>
							console.warn("[renderer:useConnection] get_status failed:", err),
						);
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
								"[renderer:useConnection] onboarding_is_first_run probe failed:",
								e,
							);
						}
					}
				}
			} catch (err) {
				//surface the swallowed get_config error to the
				// renderer console so a hung backend probe is observable
				// (previously this catch silently swallowed the error with
				// no log line, making it impossible to diagnose why the
				// connection probe kept retrying). `retries + 1` reports
				// the 1-indexed attempt number that just failed (retries
				// is 0-indexed; it's incremented below for the next loop
				// iteration). The `err` argument is passed through so the
				// underlying cause (network error, IPC rejection, etc.) is
				// visible in the devtools console alongside the attempt
				// counter.
				console.warn(
					`[renderer:useConnection] get_config connection probe failed (attempt ${retries + 1}/${maxRetries}):`,
					err,
				);
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
	//use ``get_status`` (lightweight — returns only state +
	// xrun counter) instead of ``get_config`` (serializes the entire
	//config dict).  The  heartbeat (5s backend→frontend check)
	// detects Electron crashes; this renderer→backend check detects
	// backend crashes.  Together they provide bidirectional crash
	// detection without config serialization churn.
	//
	//the previous implementation flipped to
	// ``"disconnected"`` after a SINGLE failed ``get_status`` call.
	// A transient TCP hiccup (GC pause, OS scheduler delay, brief
	// socket congestion) would mark the backend dead even though the
	// process was still alive — the user saw a jarring "Lost
	// connection" UI and had to click Retry. We now retry up to
	// ``HEALTH_CHECK_MAX_RETRIES`` times with a short backoff before
	// declaring the backend disconnected. A success at any retry
	// tier resets the failure counter and the cycle continues.
	//
	//the previous 60s interval was too coarse — a dead
	// backend could sit undetected for up to a minute before the
	//user saw any feedback (the  backend→frontend heartbeat
	// only catches ELECTRON crashes, not Python-side crashes). The
	// 15s interval catches a dead backend within ~15s of the last
	// successful probe, which is the threshold at which users
	// perceive "the app is hung" and start clicking around. The
	// 2-strike retry (``HEALTH_CHECK_MAX_RETRIES = 2``) still
	// tolerates transient flaps; the steady-state IPC load is one
	// ``get_status`` call every 15s (trivial — the response is a
	// 30-byte JSON envelope).
	//
	//the 15s probe is now redundant with the backend's push
	// events — `state_changed` is emitted on every client connect and
	// `bubble_level` / `mic_level` flow at 10-30 Hz during active use.
	// We skip the probe entirely if a push event was received within
	// the last ``HEALTH_CHECK_EVENT_GRACE_MS`` (60s) — the push
	// already proved liveness. Only when pushes have stopped for a
	// full minute do we fall back to the active ``get_status`` probe.
	// This reduces steady-state IPC load to ~0 during active use
	// while preserving the dead-backend detection guarantee (a dead
	// backend stops pushing, and 60s later the probe kicks in to
	// confirm + flip to disconnected).
	useEffect(() => {
		if (connectionStatus !== "connected") return;

		let cancelled = false;
		// Number of quick retries before declaring disconnected.
		// 3 strikes (initial attempt + 2 retries) ≈ 1s of total
		// tolerance for a transient flap before we surface the
		// outage to the user.
		const HEALTH_CHECK_MAX_RETRIES = 2;
		const HEALTH_CHECK_RETRY_DELAY_MS = 500;
		//skip the active probe if a push event was received
		//within this grace window. 60s matches the  "user
		// perceives hung" threshold — a backend that hasn't pushed
		// for 60s is either dead or stuck, and the active probe is
		// the right tool to disambiguate.
		const HEALTH_CHECK_EVENT_GRACE_MS = 60_000;
		let retryTimer: ReturnType<typeof setTimeout> | undefined;
		let failureCount = 0;

		const probe = async (isRetry: boolean): Promise<void> => {
			//skip the probe if a push event was received
			// recently — the push already proved liveness, so the
			// active `get_status` round-trip is pure waste. Still
			// reset failureCount so a transient flap that follows a
			// long quiet period doesn't carry over stale failures.
			const lastEventMs = lastEventReceivedAtRef.current;
			if (
				lastEventMs > 0 &&
				Date.now() - lastEventMs < HEALTH_CHECK_EVENT_GRACE_MS
			) {
				failureCount = 0;
				return;
			}
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
								"[renderer:useConnection] background-reconnect get_status failed:",
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

	//the supervisor's "respawn exhausted" condition is now
	// signaled via a structured `data.code: "respawn_exhausted"` field on
	// the `error` event (previously a brittle sentinel substring match).
	// When this code is present, the handler flips `connectionStatus` to
	// `"disconnected"` in addition to setting a localized `lastError` —
	// otherwise the UI stays stuck on the transient `"restarting"` banner
	// forever.
	const RESPAWN_EXHAUSTED_CODE = "respawn_exhausted";

	usePythonEvent(
		"status_change",
		useCallback(
			(data): (() => void) | undefined => {
				markEventReceived();
				if (data?.status) {
					const validated = asRecordingState(data.status);
					if (validated) {
						setRecordingState(validated);
						// The backend forwards the `set_state` message (the
						// tray-tooltip reason) with every status_change.
						// Surface it for the error state so the Home page
						// RecordingErrorCard explains what happened (e.g.
						// "No models are available…") instead of leaving
						// lastError null and showing only the bare
						// "ERROR" pill. Non-error transitions keep
						// clearing lastError exactly as before.
						setLastError(
							validated === "error" && data.message ? data.message : null,
						);
					}
				}
				return undefined;
			},
			[markEventReceived, setRecordingState, setLastError],
		),
	);

	usePythonEvent(
		"error",
		useCallback(
			(data): (() => void) | undefined => {
				markEventReceived();
				if (
					typeof data?.message === "string" ||
					typeof data?.code === "string"
				) {
					//when the supervisor exhausts its respawn
					// retries, `python-namespace.ts` synthesizes an `error`
					// event with `code: "respawn_exhausted"`. The UI must
					// transition from the transient `"restarting"` state to
					// the terminal `"disconnected"` state so the user sees
					// the "Lost connection" screen with the cause + a Retry
					// button, instead of an indefinite "Restarting…" banner.
					// Use the localized message when the structured code is
					// present; fall back to the raw message otherwise.
					if (data?.code === RESPAWN_EXHAUSTED_CODE) {
						setLastError(t("connection.respawnFailed"));
						setConnectionStatus("disconnected");
					} else if (typeof data?.message === "string") {
						setLastError(data.message);
					}
				}
				return undefined;
			},
			[markEventReceived, setLastError, setConnectionStatus, t],
		),
	);

	// ── Transient TCP recovery ───────────────────────────────────
	usePythonEvent(
		"reconnecting",
		useCallback((): (() => void) | undefined => {
			markEventReceived();
			//`"restarting"` is a member of the ConnectionStatus
			// union (see appStore.ts), so the `as ConnectionStatus` cast
			// was redundant dead code.
			setConnectionStatus("restarting");
			return undefined;
		}, [markEventReceived, setConnectionStatus]),
	);
	usePythonEvent(
		"reconnected",
		useCallback((): (() => void) | undefined => {
			markEventReceived();
			call("get_config")
				.then(() => setConnectionStatus("connected"))
				.catch(() => setConnectionStatus("disconnected"));
			return undefined;
		}, [markEventReceived, call, setConnectionStatus]),
	);

	//the backend emits `state_changed` once on every
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
				markEventReceived();
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
			[markEventReceived, setConnectionStatus, setLastError, setRecordingState],
		),
	);

	// ── Reconnection handler (called by children on fatal errors) ─

	//OPTION-A recovered retry: probe first, then escalate to a
	// main-process backend RESTART if the probe fails.
	//
	// Previously this handler was a single `get_config` probe: on
	// failure it flipped straight back to "disconnected", so a backend
	// that had actually died (not just a TCP flap) left the user in a
	// loop of clicking Retry with zero effect — the probe can never
	// succeed against a dead process, and the renderer had NO way to
	// recreate the backend.
	//
	// The fix: when the probe fails, ask the Electron main process to
	// restart ONLY the Python backend (`window.window_.restartBackend`,
	// the `backend:restart` IPC channel). The main process kills the
	// old sidecar (SIGTERM→SIGKILL fallback, exit listeners stripped)
	// and respawns it; the fresh backend then goes through the normal
	// `state_changed` push-on-connect, which flips this hook back to
	// "connected" automatically (see the `state_changed` subscriber
	// above).
	//
	// Status flow: "connecting" (probe in flight) → probe ok
	// "connected"; probe fail + restart accepted → "restarting" (the
	// ConnectionStatusScreen renders the Restarting UI + Force-Retry
	// affordance for this state); probe fail + restart declined
	// (adopted mode / relaunch in-flight / bridge missing) →
	// "disconnected" with a localized lastError so the user knows the
	// single-click recovery was attempted but the backend is
	// parent-managed.
	//
	// The post-restart recovery path lands through `state_changed`
	// (pushed by the backend on client connect) or the
	// `reconnected` synthetic event from the host bridge — both are
	// already subscribed above, so no extra wiring is needed to exit
	// the "restarting" state.
	const handleRetryConnection = useCallback(async () => {
		setConnectionStatus("connecting");
		try {
			await call("get_config");
			setConnectionStatus("connected");
			return;
		} catch {
			// Probe failed — the backend may be dead, not just flapping.
			// Escalate to a process restart (Phase 2 below).
		}
		// Phase 2 — escalate to a backend-process restart.
		try {
			const res = await window.window_?.restartBackend?.();
			if (res?.ok) {
				setConnectionStatus("restarting");
				return;
			}
			// adopted mode (backend spawned us) or relaunch in-flight —
			// nothing more the main process can do for us.
			setLastError(t("connection.restartBackendHint"));
			setConnectionStatus("disconnected");
		} catch (e) {
			// Bridge channel unavailable (tauri mode, old preload) or
			// handler threw — no restart capability, fall back to the
			// previous bare-probe behavior.
			console.warn(
				"[renderer:useConnection] restartBackend escalation failed:",
				e,
			);
			setLastError(t("connection.restartBackendHint"));
			setConnectionStatus("disconnected");
		}
	}, [call, setConnectionStatus, setLastError, t]);

	return {
		recordingState,
		connectionStatus,
		lastError,
		handleRetryConnection,
	};
}
