// @vitest-environment node
/**
 * Tests for `dedupeRepeatedLogs`: consecutive-identical log-line
 * collapse with an (xN) repeat-count summary.
 *
 * Pins the contract:
 *   - first occurrence forwards unchanged (no latency, no suffix)
 *   - identical repeats are suppressed
 *   - a streak of >= 2 emits ONE `(xN)` summary when the streak breaks,
 *     where N is the TOTAL occurrence count
 *   - a lone occurrence (count 1) never emits a summary
 *   - different args (e.g. different `cmd`) are independent streaks
 *   - a streak that keeps growing re-emits a cumulative `(xN)` heartbeat
 *     every `flushIntervalMs`, but an idle streak goes silent (no phantom
 *     summaries) and re-arms when a flood resumes (fake timers)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dedupeRepeatedLogs } from "../dedupeRepeatedLogs";

describe("dedupeRepeatedLogs", () => {
	// Type the mock as the exact sink signature the wrapper accepts so
	// vitest v4's stricter ``Mock<Constructable | Procedure>`` typing
	// doesn't reject ``dedupeRepeatedLogs(emit)`` at every call site.
	let emit: ReturnType<typeof vi.fn<(msg: string, ...args: unknown[]) => void>>;

	beforeEach(() => {
		emit = vi.fn<(msg: string, ...args: unknown[]) => void>();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	it("forwards the first occurrence unchanged", () => {
		const log = dedupeRepeatedLogs(emit);
		log("python-call rejected", {
			cmd: "get_config",
			code: "backend_not_connected",
		});

		expect(emit).toHaveBeenCalledTimes(1);
		expect(emit).toHaveBeenCalledWith(
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
		);
	});

	it("suppresses identical repeats and emits a single (xN) summary on streak break", () => {
		const log = dedupeRepeatedLogs(emit);
		const args = { cmd: "get_config", code: "backend_not_connected" };
		log("python-call rejected", args);
		log("python-call rejected", args);
		log("python-call rejected", args);
		expect(emit).toHaveBeenCalledTimes(1); // first only so far

		// Streak breaks with a different message.
		log("python-call rejected", {
			cmd: "get_status",
			code: "backend_not_connected",
		});

		expect(emit).toHaveBeenCalledTimes(3);
		expect(emit.mock.calls[0]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
		]);
		expect(emit.mock.calls[1]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
			"(x3)",
		]);
		expect(emit.mock.calls[2]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_status" }),
		]);
	});

	it("does not emit a summary for a lone occurrence", () => {
		const log = dedupeRepeatedLogs(emit);
		log("python-call rejected", {
			cmd: "get_config",
			code: "backend_not_connected",
		});
		// Different message → old streak (count 1) must NOT summarize.
		log("tcp connected", { port: 9876 });

		expect(emit).toHaveBeenCalledTimes(2);
		expect(emit.mock.calls[1]).toEqual(["tcp connected", { port: 9876 }]);
	});

	it("treats the same message with different args as independent streaks", () => {
		const log = dedupeRepeatedLogs(emit);
		const getConfig = { cmd: "get_config", code: "backend_not_connected" };
		const getStatus = { cmd: "get_status", code: "backend_not_connected" };
		log("python-call rejected", getConfig);
		log("python-call rejected", getConfig); // suppressed (streak A = 2)
		// Different cmd breaks streak A → (x2) summary.
		log("python-call rejected", getStatus);
		// Second get_config burst is a FRESH streak, not merged with A.
		log("python-call rejected", getConfig);
		log("python-call rejected", getConfig); // suppressed (streak B = 2)

		expect(emit).toHaveBeenCalledTimes(4);
		expect(emit.mock.calls[0]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
		]);
		expect(emit.mock.calls[1]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
			"(x2)",
		]);
		expect(emit.mock.calls[2]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_status" }),
		]);
		// The second get_config burst started fresh (count 1) — it was NOT
		// merged into the earlier (x2) summary.
		expect(emit.mock.calls[3]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
		]);
	});

	it("emits a cumulative (xN) heartbeat while a streak keeps growing", () => {
		vi.useFakeTimers();
		const log = dedupeRepeatedLogs(emit, { flushIntervalMs: 60_000 });
		const args = { cmd: "get_config", code: "backend_not_connected" };

		log("python-call rejected", args);
		log("python-call rejected", args);
		log("python-call rejected", args);
		expect(emit).toHaveBeenCalledTimes(1);

		// 60s of continued rejects → heartbeat summary with the total.
		vi.advanceTimersByTime(60_000);
		expect(emit).toHaveBeenCalledTimes(2);
		expect(emit.mock.calls[1]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
			"(x3)",
		]);

		// More rejects, another 60s → cumulative count grows.
		log("python-call rejected", args);
		log("python-call rejected", args);
		vi.advanceTimersByTime(60_000);
		expect(emit).toHaveBeenCalledTimes(3);
		expect(emit.mock.calls[2]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
			"(x5)",
		]);
	});

	it("goes silent once a streak stops growing (no phantom summaries)", () => {
		vi.useFakeTimers();
		const log = dedupeRepeatedLogs(emit, { flushIntervalMs: 60_000 });
		const args = { cmd: "get_config", code: "backend_not_connected" };

		// A short burst that then goes quiet (backend recovered, next
		// calls succeed and log nothing). The heartbeat must emit the
		// (x3) summary ONCE and then stop — not every 60s forever.
		log("python-call rejected", args);
		log("python-call rejected", args);
		log("python-call rejected", args);

		vi.advanceTimersByTime(60_000);
		expect(emit).toHaveBeenCalledTimes(2);
		expect(emit.mock.calls[1]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
			"(x3)",
		]);

		// The streak is idle now — advancing time must NOT emit more.
		vi.advanceTimersByTime(120_000);
		expect(emit).toHaveBeenCalledTimes(2);
	});

	it("re-arms the heartbeat when a flood resumes after going idle", () => {
		vi.useFakeTimers();
		const log = dedupeRepeatedLogs(emit, { flushIntervalMs: 60_000 });
		const args = { cmd: "get_config", code: "backend_not_connected" };

		log("python-call rejected", args);
		log("python-call rejected", args);
		log("python-call rejected", args);
		vi.advanceTimersByTime(60_000); // (x3), then idle → timer stops

		// Flood resumes later — next repeat re-arms the heartbeat.
		log("python-call rejected", args);
		log("python-call rejected", args);
		vi.advanceTimersByTime(60_000);
		expect(emit).toHaveBeenCalledTimes(3);
		expect(emit.mock.calls[2]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_config" }),
			"(x5)",
		]);
	});

	it("does not duplicate a summary when the streak breaks right after a heartbeat tick", () => {
		vi.useFakeTimers();
		const log = dedupeRepeatedLogs(emit, { flushIntervalMs: 60_000 });
		const args = { cmd: "get_config", code: "backend_not_connected" };

		log("python-call rejected", args);
		log("python-call rejected", args);
		log("python-call rejected", args);
		vi.advanceTimersByTime(60_000); // heartbeat already emitted (x3)

		// The streak breaks with a different message — the break must NOT
		// re-emit (x3): it was already reported by the heartbeat.
		log("python-call rejected", {
			cmd: "get_status",
			code: "backend_not_connected",
		});

		expect(emit).toHaveBeenCalledTimes(3);
		expect(emit.mock.calls[2]).toEqual([
			"python-call rejected",
			expect.objectContaining({ cmd: "get_status" }),
		]);
	});

	it("stops the heartbeat once the streak breaks", () => {
		vi.useFakeTimers();
		const log = dedupeRepeatedLogs(emit, { flushIntervalMs: 60_000 });
		const args = { cmd: "get_config", code: "backend_not_connected" };

		log("python-call rejected", args);
		log("python-call rejected", args);

		// Streak breaks before the heartbeat fires.
		log("python-call rejected", {
			cmd: "get_status",
			code: "backend_not_connected",
		});
		expect(emit).toHaveBeenCalledTimes(3); // first, (x2), get_status first

		// Advancing time must NOT emit more lines (timer was cleared).
		vi.advanceTimersByTime(60_000);
		vi.advanceTimersByTime(60_000);
		expect(emit).toHaveBeenCalledTimes(3);
	});

	it("keys on the message text (two different messages never merge)", () => {
		const log = dedupeRepeatedLogs(emit);
		log("python-call rejected", { cmd: "get_config" });
		log("python-call failed", { cmd: "get_config", code: "command_failed" });
		log("python-call rejected", { cmd: "get_config" });

		expect(emit).toHaveBeenCalledTimes(3);
		// No summaries: every streak had count 1.
		expect(emit.mock.calls.every((c) => c.length === 2)).toBe(true);
	});
});
