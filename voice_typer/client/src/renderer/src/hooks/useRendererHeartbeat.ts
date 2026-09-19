import { useEffect } from "react";

/** Beat interval while the window is visible (must equal the Rust
 * `HEARTBEAT_INTERVAL_SECS`). */
export const HEARTBEAT_INTERVAL_MS = 10_000;

/** Test seam: the bridge call, so tests can observe beats without a
 * real host. */
export const _heartbeatForTests = {
	send: (): Promise<void> | undefined => window.window_?.heartbeat?.(),
};

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
