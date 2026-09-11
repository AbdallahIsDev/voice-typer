/**
 * TCP retry scheduler: generation-checked exponential backoff.
 *
 * Split out of `tcp-connect.ts`. Owns the post-close retry decision:
 * the relaunch guard, the live-process guard, the retry counter/log
 * line, and the backoff timer stored on `state._tcpRetryTimer`.
 */
import { log } from "../../logging";
import { state } from "../../state";
import { tcpRetryDelay } from "./retry-delay";

/**
 * Schedule the next connect attempt after a socket close. Called by the
 * close handler AFTER its retry-generation check, so a stale-socket
 * close never reaches this function.
 */
export function scheduleTcpRetryAfterClose(
	port: number,
	tryConnect: () => void,
): void {
	// If a full app relaunch is in flight, the process is
	// about to exit, no point scheduling retries.
	if (state._relaunching) {
		return;
	}
	// Only retry while a Python process is alive.
	// Do NOT check pythonReady here - that's false until the first
	// successful connection.  The error handler no longer schedules
	// retries (to prevent exponential multiplication), so the close
	// handler must cover the initial startup case too.
	if (state.pythonProcess !== null) {
		state._tcpRetryCount++;
		if (state._tcpRetryCount === 1) {
			log.warn(`[TCP] waiting for Python backend (127.0.0.1:${port})...`);
		} else {
			log.warn(
				"[TCP] Python backend not ready yet (127.0.0.1:" +
					port +
					") -- retrying (attempt " +
					state._tcpRetryCount +
					")",
			);
		}
		// Exponential backoff via tcpRetryDelay (250ms base, 1s cap):
		// fast first retry so a quick backend bind isn't missed, capped
		// so a slow cold start (multi-second interpreter + imports)
		// isn't hammered, each attempt is one localhost SYN refused
		// at the kernel while nothing listens.
		const delay = tcpRetryDelay(state._tcpRetryCount);
		// store the retry timer on shared state so
		// `stopPython()` and `relaunchApp()` can clearTimeout
		// it before bumping `_tcpRetryGeneration`. Previously
		// the timer was a fire-and-forget local, even after
		// `state._tcpRetryGeneration++` invalidated the
		// generation check at the top of `tryConnect()`, the
		// pending timer still fired `tryConnect()` once more
		// (creating a fresh socket that immediately hit the
		// generation mismatch and bailed). Clearing the timer
		// explicitly in `stopPython()` / `relaunchApp()`
		// short-circuits this.
		if (state._tcpRetryTimer) {
			clearTimeout(state._tcpRetryTimer);
		}
		state._tcpRetryTimer = setTimeout(() => {
			state._tcpRetryTimer = null;
			tryConnect();
		}, delay);
	}
}
