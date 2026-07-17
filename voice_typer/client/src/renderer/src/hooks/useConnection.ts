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
	 * F1 (b-review Finding 5): the first-run check is now performed
	 * unconditionally on the initial connection probe regardless of
	 * the persisted page (see the effect body for rationale), so this
	 * field is no longer read inside the hook. It's kept in the
	 * interface for backward compatibility with existing callers
	 * (App.tsx) and to leave the door open for future page-aware
	 * routing logic.
	 */
	currentPage: Page;
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
						.catch(() => {});
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
						} catch {
							// Older backend without the IPC route — silently ignore.
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
	// detects Electron crashes; this 60s renderer→backend check
	// detects backend crashes.  Together they provide bidirectional
	// crash detection without config serialization churn.
	useEffect(() => {
		if (connectionStatus !== "connected") return;

		let cancelled = false;

		const interval = setInterval(async () => {
			try {
				await call("get_status");
			} catch {
				if (!cancelled) setConnectionStatus("disconnected");
			}
		}, 60_000);

		return () => {
			cancelled = true;
			clearInterval(interval);
		};
	}, [connectionStatus, call, setConnectionStatus]);

	// ── App-level event subscriptions ─────────────────────────────

	usePythonEvent(
		"status_change",
		useCallback(
			(data) => {
				if (data?.status) {
					const validated = asRecordingState(data.status);
					if (validated) {
						setRecordingState(validated);
						setLastError(null);
					}
				}
			},
			[setRecordingState, setLastError],
		),
	);

	usePythonEvent(
		"error",
		useCallback(
			(data) => {
				if (typeof data?.message === "string") {
					setLastError(data.message);
				}
			},
			[setLastError],
		),
	);

	// ── Transient TCP recovery ───────────────────────────────────
	usePythonEvent(
		"reconnecting",
		useCallback(
			// UX-22: `"restarting"` is a member of the ConnectionStatus
			// union (see appStore.ts), so the `as ConnectionStatus` cast
			// was redundant dead code.
			() => setConnectionStatus("restarting"),
			[setConnectionStatus],
		),
	);
	usePythonEvent(
		"reconnected",
		useCallback(() => {
			call("get_config")
				.then(() => setConnectionStatus("connected"))
				.catch(() => setConnectionStatus("disconnected"));
		}, [call, setConnectionStatus]),
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
