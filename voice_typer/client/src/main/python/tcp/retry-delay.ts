/**
 * Pure TCP retry backoff for the Python backend connect loop.
 *
 * Split from `retry-scheduler.ts` so the delay sequence is unit-testable
 * without mocking shared state. Base 250ms, doubling, capped at 1s: each
 * attempt is one localhost SYN that the kernel refuses instantly while
 * nothing listens, so polling at up to 1Hz costs nothing and bounds the
 * post-ready dead wait (with the old 2s cap a backend that bound just
 * after a failed attempt sat idle ~2s before the next try).
 */
export const TCP_RETRY_BASE_MS = 250;
export const TCP_RETRY_MAX_MS = 1000;

export function tcpRetryDelay(retryCount: number): number {
	return Math.min(TCP_RETRY_BASE_MS * 2 ** (retryCount - 1), TCP_RETRY_MAX_MS);
}
