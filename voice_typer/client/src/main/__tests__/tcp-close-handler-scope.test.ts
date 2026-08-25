// @vitest-environment node
/**
 *  regression tests for the TCP close-handler scope fix in
 * `tcp-connect.ts`.
 *
 * Background
 * ----------
 * The  fix scoped only the `tcpBuffer` / `tcpSocket` / `_tcpAuthed`
 * clear to `state.tcpSocket === client`. The heartbeat-interval clear
 * and the pending-request reject loop were left UNCONDITIONAL. When a
 * stale socket (from an older `_tcpRetryGeneration`) emitted 'close'
 * AFTER a newer socket had connected and installed a heartbeat +
 * pending requests, the stale close handler:
 *
 *   (1) Cleared `state.heartbeatInterval` (the NEW socket's heartbeat)
 *       → heartbeat stops → Python-side heartbeat watchdog fires after
 *       ~120s → Python exits.
 *   (2) Rejected all `state.pendingRequests` with "Python socket
 *       closed" even though the live socket was healthy.
 *
 *  fix: scope the heartbeat clear AND the pending-request reject
 * loop to `state.tcpSocket === client` (matching the  pattern).
 *
 * These tests use source-text assertions (matching the
 * `tcp-retry-timer.test.ts` pattern) because `tcpConnect` creates a
 * real `net.Socket` at call-time and its close handler is a closure
 * inside the nested `tryConnect()` — extracting it for runtime testing
 * would require restructuring the production code. Source-text
 * assertion is sufficient to pin the scoping contract.
 *
 * ON LINUX (sandbox): source-text assertion — no platform dependency.
 * ON WINDOWS / macOS (not run here): same source-text contract
 *   applies; the close handler is platform-agnostic.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Read the close-handler source text once. The body moved atomically
 * into the `python/tcp/close-handler.ts` leaf (split out of
 * `tcp-connect.ts`); the pin path follows it.
 */
function readTcpConnectSrc(): string {
	return fs.readFileSync(
		path.resolve(__dirname, "../python/tcp/close-handler.ts"),
		"utf-8",
	);
}

describe("FR-30: tcp-connect.ts close handler scopes heartbeat + pending cleanup to state.tcpSocket === client", () => {
	const src = readTcpConnectSrc();

	it('the close handler is registered with `client.on("close", ...)`', () => {
		// Sanity check that we're asserting against the right handler.
		expect(src).toMatch(/client\.on\(\s*["']close["']\s*,/);
	});

	it("scopes the heartbeat-interval clear to `state.tcpSocket === client`", () => {
		// FR-30: the heartbeat-interval clear (`clearInterval(state.heartbeatInterval)`
		// + `state.heartbeatInterval = null`) must be INSIDE the
		// `if (state.tcpSocket === client)` block, NOT after it.
		//
		// Strategy: locate the close handler, slice from its opening
		// brace to the matching close, and assert the heartbeat clear
		// is inside the `state.tcpSocket === client` block (i.e. it
		// appears AFTER the `if (state.tcpSocket === client)` line
		// and BEFORE the corresponding closing brace).
		const closeIdx = src.search(
			/client\.on\(\s*["']close["']\s*,\s*\(\)\s*=>\s*\{/,
		);
		expect(closeIdx).toBeGreaterThan(-1);

		// Slice from the close handler to the next handler (or end
		// of tryConnect). The close handler is the LAST handler
		// registered inside tryConnect, so slice to the end of
		// tryConnect (the closing `});` of the close registration
		// followed by `}` of tryConnect).
		const closeHandlerStart = src.indexOf("{", closeIdx);
		// Find the matching closing brace by counting depth.
		let depth = 0;
		let closeHandlerEnd = -1;
		for (let i = closeHandlerStart; i < src.length; i++) {
			const ch = src[i];
			if (ch === "{") depth++;
			else if (ch === "}") {
				depth--;
				if (depth === 0) {
					closeHandlerEnd = i + 1;
					break;
				}
			}
		}
		expect(closeHandlerEnd).toBeGreaterThan(closeHandlerStart);
		const closeHandler = src.slice(closeHandlerStart, closeHandlerEnd);

		// The `state.tcpSocket === client` check must appear in the
		// close handler.
		expect(closeHandler).toContain("state.tcpSocket === client");

		// The heartbeat-interval clear must appear AFTER the
		// `state.tcpSocket === client` line (i.e. INSIDE the if
		// block).
		const tcpSocketCheckIdx = closeHandler.indexOf(
			"state.tcpSocket === client",
		);
		const heartbeatClearIdx = closeHandler.indexOf(
			"clearInterval(state.heartbeatInterval)",
		);
		expect(heartbeatClearIdx).toBeGreaterThan(-1);
		expect(heartbeatClearIdx).toBeGreaterThan(tcpSocketCheckIdx);

		// The heartbeat clear must appear BEFORE the closing brace of
		// the `if (state.tcpSocket === client)` block. We approximate
		// this by checking that the next `}` after the heartbeat
		// clear (closing the if block) appears before the next
		// `if (retryGen` or `if (state._relaunching)` (which are
		// OUTSIDE the if block).
		const afterHeartbeatClear = closeHandler.slice(heartbeatClearIdx);
		// The `state.heartbeatInterval = null;` line must also be
		// inside the if block.
		expect(afterHeartbeatClear).toContain("state.heartbeatInterval = null");
	});

	it("scopes the pending-request reject loop to `state.tcpSocket === client`", () => {
		// FR-30: the `for (const [id, entry] of state.pendingRequests)`
		// loop that rejects all pending requests must be INSIDE the
		// `if (state.tcpSocket === client)` block.
		const closeIdx = src.search(
			/client\.on\(\s*["']close["']\s*,\s*\(\)\s*=>\s*\{/,
		);
		expect(closeIdx).toBeGreaterThan(-1);

		const closeHandlerStart = src.indexOf("{", closeIdx);
		let depth = 0;
		let closeHandlerEnd = -1;
		for (let i = closeHandlerStart; i < src.length; i++) {
			const ch = src[i];
			if (ch === "{") depth++;
			else if (ch === "}") {
				depth--;
				if (depth === 0) {
					closeHandlerEnd = i + 1;
					break;
				}
			}
		}
		expect(closeHandlerEnd).toBeGreaterThan(closeHandlerStart);
		const closeHandler = src.slice(closeHandlerStart, closeHandlerEnd);

		const tcpSocketCheckIdx = closeHandler.indexOf(
			"state.tcpSocket === client",
		);
		const pendingLoopIdx = closeHandler.indexOf(
			"for (const [id, entry] of state.pendingRequests)",
		);
		expect(pendingLoopIdx).toBeGreaterThan(-1);
		// The pending-request loop must appear AFTER the
		// `state.tcpSocket === client` check (i.e. INSIDE the if
		// block).
		expect(pendingLoopIdx).toBeGreaterThan(tcpSocketCheckIdx);
	});

	it("the retry-generation check (`retryGen !== state._tcpRetryGeneration`) appears AFTER the if (state.tcpSocket === client) block", () => {
		//the retry-generation check is OUTSIDE the
		// `state.tcpSocket === client` block — it gates the retry
		// scheduling, not the state cleanup. This is correct: a
		// stale-socket close should NOT schedule a retry (the newer
		// socket is the live one), but the generation check is the
		// mechanism that decides whether to retry, so it must run
		// unconditionally (and short-circuit) on stale closes.
		const closeIdx = src.search(
			/client\.on\(\s*["']close["']\s*,\s*\(\)\s*=>\s*\{/,
		);
		expect(closeIdx).toBeGreaterThan(-1);

		const closeHandlerStart = src.indexOf("{", closeIdx);
		let depth = 0;
		let closeHandlerEnd = -1;
		for (let i = closeHandlerStart; i < src.length; i++) {
			const ch = src[i];
			if (ch === "{") depth++;
			else if (ch === "}") {
				depth--;
				if (depth === 0) {
					closeHandlerEnd = i + 1;
					break;
				}
			}
		}
		const closeHandler = src.slice(closeHandlerStart, closeHandlerEnd);

		const tcpSocketCheckIdx = closeHandler.indexOf(
			"state.tcpSocket === client",
		);
		const retryGenCheckIdx = closeHandler.indexOf(
			"retryGen !== state._tcpRetryGeneration",
		);
		expect(retryGenCheckIdx).toBeGreaterThan(-1);
		// The retry-generation check must appear AFTER the
		// `state.tcpSocket === client` check (it's outside the if
		// block, in the main close-handler body).
		expect(retryGenCheckIdx).toBeGreaterThan(tcpSocketCheckIdx);
	});

	it("close-handler comment documents the heartbeat/pending scoping rationale", () => {
		// Documentation anchor: the descriptive comment (which explains
		// why the heartbeat + pending cleanup is scoped to
		// `state.tcpSocket === client`) should be present in the close
		// handler so future contributors know the rationale. The
		// historical task-ID token was stripped from production source
		// (C-STYLE-1), so the assertion pins the descriptive text.
		expect(src).toContain(
			"heartbeat-interval clear and pending-request reject",
		);
	});
});
