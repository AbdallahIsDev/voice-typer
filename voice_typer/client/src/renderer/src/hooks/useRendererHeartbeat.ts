/**
 * useRendererHeartbeat, feeds the host's webview liveness watchdog
 * (review.md MO-113).
 *
 * predecessor logged `child-process-gone` (GPU / renderer crashes) from the
 * main process. No Tauri/wry platform surfaces an equivalent renderer
 * crash event, so the host watches whether this renderer's JavaScript is
 * still RUNNING: a lightweight `renderer_heartbeat` invoke every
 * {@link HEARTBEAT_INTERVAL_MS}, and `platform::renderer_watchdog` logs an
 * ERROR when the beats stop while the main window is visible.
 *
 * Must stay in sync with the Rust constants
 * (`src-tauri/src/platform/renderer_watchdog.rs`): the interval here and
 * `HEARTBEAT_INTERVAL_SECS` are pinned against each other by
 * `renderer_watchdog_tests.rs`.
 *
 * Guards:
 * - Beats are only sent while `document.visibilityState === "visible"`.
 *   Background windows legitimately have their timers throttled by every
 *   engine (WebView2 background throttling, macOS App Nap), so sending
 *   from a hidden window would either be throttled into a false stall or
 *   keep the host's watchdog armed for a window nobody can see. The host
 *   independently requires the window to be visible + un-minimized.
 * - Beats stop on unmount; failures are swallowed (the bridge may be
 *   absent in tests / the bubble runtime, and a failed beat must never
 *   surface as an app error).
 */
import { useEffect } from "react";

/** Beat interval while the window is visible (must equal the Rust
 * `HEARTBEAT_INTERVAL_SECS`). */
export const HEARTBEAT_INTERVAL_MS = 10_000;

/** Test seam: the bridge call, so tests can observe beats without a
 * real host. */
export const _heartbeatForTests = {
	send: (): Promise<void> | undefined => window.window_?.heartbeat?.(),
};

/**
 * Send one heartbeat now and then on an interval, but ONLY while the
 * document is visible. Restarts on visibility changes so returning to
 * the window resumes beating immediately.
 */
export function useRendererHeartbeat(): void {
	useEffect(() => {
		let timer: ReturnType<typeof setInterval> | null = null;

		const beat = () => {
			try {
				void _heartbeatForTests.send()?.catch(() => {
					// Best-effort telemetry: a failed beat must never
					// become an unhandled rejection.
				});
			} catch {
				// Same contract: never throw from the heartbeat.
			}
		};

		const start = () => {
			if (timer !== null) return;
			beat();
			timer = setInterval(beat, HEARTBEAT_INTERVAL_MS);
		};

		const stop = () => {
			if (timer === null) return;
			clearInterval(timer);
			timer = null;
		};

		const onVisibilityChange = () => {
			if (document.visibilityState === "visible") {
				start();
			} else {
				stop();
			}
		};

		if (document.visibilityState === "visible") {
			start();
		}
		document.addEventListener("visibilitychange", onVisibilityChange);

		return () => {
			stop();
			document.removeEventListener("visibilitychange", onVisibilityChange);
		};
	}, []);
}
