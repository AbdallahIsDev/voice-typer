import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePython } from "@/hooks/usePython";
import { __resetPythonSingleFlightForTests } from "@/lib/python-bridge/usePython";

interface PythonBridgeMock {
	call: ReturnType<typeof vi.fn>;
	onEvent: ReturnType<typeof vi.fn>;
}

function installPythonMock(callImpl: (...args: unknown[]) => Promise<unknown>) {
	const mock: PythonBridgeMock = {
		call: vi.fn(callImpl),
		onEvent: vi.fn(() => () => {}),
	};
	(window as unknown as { python: PythonBridgeMock }).python = mock;
	return mock;
}

function timeoutError(cmd: string): Error {
	return new Error(`IPC command "${cmd}" timed out after 5000ms`);
}

beforeEach(() => {
	__resetPythonSingleFlightForTests();
	delete (window as unknown as { python?: PythonBridgeMock }).python;
});

describe("usePython cold-start retry for idempotent reads", () => {
	it("retries a timeout-shaped get_config failure, then resolves", async () => {
		let attempts = 0;
		const bridge = installPythonMock(() => {
			attempts += 1;
			return attempts < 3
				? Promise.reject(timeoutError("get_config"))
				: Promise.resolve({ ok: true });
		});
		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).resolves.toEqual({
			ok: true,
		});
		expect(bridge.call).toHaveBeenCalledTimes(3);
	}, 10000);

	it("gives up after the wait deadline and throws the last error", async () => {
		// Fake timers: the mock rejects instantly, so only the backoff
		// schedule consumes (fake) time — no real 90s wait.
		vi.useFakeTimers();
		try {
			const bridge = installPythonMock(() =>
				Promise.reject(timeoutError("get_config")),
			);
			const { result } = renderHook(() => usePython());

			let settled: "pending" | "rejected" = "pending";
			let failure: unknown = null;
			const pending = result.current.call("get_config").then(
				() => {},
				(err: unknown) => {
					settled = "rejected";
					failure = err;
				},
			);
			await act(async () => {
				await vi.advanceTimersByTimeAsync(100_000);
			});
			await pending;

			expect(settled).toBe("rejected");
			expect(String((failure as Error)?.message ?? failure)).toMatch(
				/timed out after 5000ms/,
			);
			// Deadline-bounded, not attempt-counted: far more patience
			// than the old 1+3 budget, but finite.
			const attempts = bridge.call.mock.calls.length;
			expect(attempts).toBeGreaterThan(4);
			await act(async () => {
				await vi.advanceTimersByTimeAsync(120_000);
			});
			expect(bridge.call.mock.calls.length).toBe(attempts);
		} finally {
			vi.useRealTimers();
		}
	});

	it("resolves when the backend answers mid-wait (slow boot, no error screen)", async () => {
		vi.useFakeTimers();
		try {
			const t0 = Date.now();
			const bridge = installPythonMock(() =>
				Date.now() - t0 < 60_000
					? Promise.reject(timeoutError("get_config"))
					: Promise.resolve({ ok: true }),
			);
			const { result } = renderHook(() => usePython());

			let outcome: unknown = null;
			const pending = result.current.call("get_config").then(
				(v: unknown) => {
					outcome = v;
				},
				() => {
					outcome = "rejected";
				},
			);
			await act(async () => {
				await vi.advanceTimersByTimeAsync(100_000);
			});
			await pending;

			expect(outcome).toEqual({ ok: true });
			expect(bridge.call.mock.calls.length).toBeGreaterThan(2);
		} finally {
			vi.useRealTimers();
		}
	});

	it("does NOT retry non-timeout errors", async () => {
		const bridge = installPythonMock(() => Promise.reject(new Error("down")));
		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).rejects.toThrow("down");
		expect(bridge.call).toHaveBeenCalledTimes(1);
	});

	it("does NOT retry writes even when timeout-shaped", async () => {
		const bridge = installPythonMock(() =>
			Promise.reject(timeoutError("set_config")),
		);
		const { result } = renderHook(() => usePython());

		// A retried set_config could apply twice: writes stay single-shot.
		await expect(
			result.current.call("set_config", { theme_mode: "dark" }),
		).rejects.toThrow(/timed out/);
		expect(bridge.call).toHaveBeenCalledTimes(1);
	});

	it("stops retrying after the bridge delivered its first success", async () => {
		const bridge = installPythonMock(() => Promise.resolve({ ok: true }));
		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_status")).resolves.toEqual({
			ok: true,
		});

		bridge.call.mockImplementation(() =>
			Promise.reject(timeoutError("get_config")),
		);
		await expect(result.current.call("get_config")).rejects.toThrow(
			/timed out/,
		);
		// No retry: post-first-success behavior is exactly what it was.
		expect(bridge.call).toHaveBeenCalledTimes(2);
	});

	it("concurrent sharers share one retry chain", async () => {
		let attempts = 0;
		const bridge = installPythonMock(() => {
			attempts += 1;
			return attempts < 2
				? Promise.reject(timeoutError("get_config"))
				: Promise.resolve({ ok: true });
		});
		const { result } = renderHook(() => usePython());

		const first = result.current.call("get_config");
		const second = result.current.call("get_config");
		await expect(first).resolves.toEqual({ ok: true });
		await expect(second).resolves.toEqual({ ok: true });
		expect(bridge.call).toHaveBeenCalledTimes(2);
	}, 10000);
});
