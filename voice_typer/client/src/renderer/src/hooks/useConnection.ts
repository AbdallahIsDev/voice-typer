import { useCallback, useEffect, useState } from "react";
import { usePythonEvent } from "@/hooks/usePython";
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

type ConnectionStatus =
	| "connected"
	| "disconnected"
	| "connecting"
	| "restarting";

interface UseConnectionArgs {
	/** Python bridge `call` function (from usePython). */
	call: <T = unknown>(
		type: string,
		data?: Record<string, unknown>,
	) => Promise<T>;
	/** Current page (used for onboarding first-run auto-routing check). */
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
 * @param args.call         Python bridge call fn.
 * @param args.currentPage  Current page (for onboarding first-run auto-route).
 * @param args.navigate     Navigation fn (for onboarding first-run auto-route).
 */
export function useConnection({
	call,
	currentPage,
	navigate,
}: UseConnectionArgs) {
	const [recordingState, setRecordingState] = useState<RecordingState>("idle");
	const [connectionStatus, setConnectionStatus] =
		useState<ConnectionStatus>("connecting");
	const [lastError, setLastError] = useState<string | null>(null);

	// ── Connection lifecycle ──────────────────────────────────────

	useEffect(() => {
		// NEW-TS-015: removed the ``if (!isReady) return`` guard — it was
		// dead code (``isReady`` was always ``true``).

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
					// Sync current state from backend (status_change events sent before
					// the React app mounted are lost — this ensures we catch up)
					call<{ status: string }>("get_status")
						.then((s) => {
							// NEW-TS-012: removed the ``as RecordingState`` cast.
							// Casting an unvalidated string to a string-literal union
							// type hides bugs — if the backend ever emits a value
							// outside the union, the cast silently produces an invalid
							// RecordingState that the rest of the type system trusts.
							// We now validate at runtime and discard unknown values.
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
					// to the wizard. Previously this 275-line component was dead
					// code. The backend's `onboarding_is_first_run` IPC route
					// checks config.onboarding_completed (and the marker file).
					// We only auto-route on the very first successful connection
					// (when currentPage is still the default 'home'); once the
					// user navigates away we don't force them back.
					if (currentPage === "home" && !cancelled) {
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
	}, [call, currentPage, navigate]);

	// Periodic health check while connected
	useEffect(() => {
		if (connectionStatus !== "connected") return;

		let cancelled = false;

		const interval = setInterval(async () => {
			try {
				await call("get_config");
			} catch {
				if (!cancelled) setConnectionStatus("disconnected");
			}
		}, 60_000);

		return () => {
			cancelled = true;
			clearInterval(interval);
		};
	}, [connectionStatus, call]);

	// ── App-level event subscriptions ─────────────────────────────

	usePythonEvent(
		"status_change",
		useCallback((data) => {
			// NEW-TS-012: validate at runtime instead of casting to RecordingState.
			if (data?.status) {
				const validated = asRecordingState(data.status);
				if (validated) {
					setRecordingState(validated);
					setLastError(null);
				}
			}
		}, []),
	);

	usePythonEvent(
		"error",
		useCallback((data) => {
			if (typeof data?.message === "string") {
				setLastError(data.message);
			}
		}, []),
	);

	// ── Transient TCP recovery ───────────────────────────────────────
	// The main process emits a synthetic "reconnected" event when the
	// TCP channel comes back after a transient drop (sleep/resume,
	// network blip).  Without this, connectionStatus gets stuck on
	// "disconnected" because the TCP close handler rejects every
	// pending request and nothing else in the main process pokes the
	// renderer when TCP silently comes back.
	//
	// NOTE: the full app restart (tray "Restart" menu item) no longer
	// needs renderer-side recovery — the entire Electron process is
	// relaunched, so the renderer boots fresh.  This handler only
	// covers transient TCP drops that don't merit a full process
	// restart.  The "reconnecting" event is kept for backward
	// compatibility (a future main-process change could emit it
	// before a transient drop), but the current main process doesn't
	// send it — it only sends "reconnected" on a successful reconnect.
	usePythonEvent(
		"reconnecting",
		useCallback(() => setConnectionStatus("restarting"), []),
	);
	usePythonEvent(
		"reconnected",
		useCallback(() => {
			call("get_config")
				.then(() => setConnectionStatus("connected"))
				.catch(() => setConnectionStatus("disconnected"));
		}, [call]),
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
	}, [call]);

	return {
		recordingState,
		connectionStatus,
		lastError,
		handleRetryConnection,
	};
}
