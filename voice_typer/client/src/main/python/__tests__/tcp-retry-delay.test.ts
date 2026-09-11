// @vitest-environment node
/**
 * Pins the TCP retry backoff sequence: 250ms base, doubling, 1s cap.
 * Guards the cold-start connect latency (post-ready dead wait ≤ ~1s)
 * without hammering the port (worst steady rate 1 localhost SYN/sec).
 */
import { describe, expect, it } from "vitest";
import {
	TCP_RETRY_BASE_MS,
	TCP_RETRY_MAX_MS,
	tcpRetryDelay,
} from "../tcp/retry-delay";

describe("tcpRetryDelay", () => {
	it("starts at the 250ms base and doubles to the 1s cap", () => {
		expect(tcpRetryDelay(1)).toBe(250);
		expect(tcpRetryDelay(2)).toBe(500);
		expect(tcpRetryDelay(3)).toBe(1000);
	});

	it("stays capped for late attempts", () => {
		for (const n of [4, 5, 6, 10, 50]) {
			expect(tcpRetryDelay(n)).toBe(1000);
		}
	});

	it("exports match the pinned sequence", () => {
		expect(TCP_RETRY_BASE_MS).toBe(250);
		expect(TCP_RETRY_MAX_MS).toBe(1000);
	});
});
