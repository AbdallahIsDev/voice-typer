/**
 * Single-flight coverage for the renderer's IPC `call` pipe
 * (`lib/python-bridge/usePython.ts`).
 *
 * Startup mounts several independent readers at once, and dev StrictMode
 * double-invokes mount effects, so identical reads overlap in flight.
 * Concurrent calls with the same command + payload must share one
 * underlying `window.python.call`; sequential calls must still re-fetch
 * (freshness), different payloads must not share, and writes must never
 * share.
 */
import { renderHook } from "@testing-library/react";
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

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

beforeEach(() => {
	__resetPythonSingleFlightForTests();
	delete (window as unknown as { python?: PythonBridgeMock }).python;
});

describe("usePython single-flight for concurrent identical reads", () => {
	it.each([
		"get_config",
		"get_status",
		"get_model_status",
		"get_model_catalog",
		"get_download_queue",
		"onboarding_is_first_run",
	])("shares one underlying call for concurrent %s", async (type) => {
		const gate = deferred<unknown>();
		const bridge = installPythonMock(() => gate.promise);
		const { result } = renderHook(() => usePython());

		const first = result.current.call(type);
		const second = result.current.call(type);
		expect(bridge.call).toHaveBeenCalledTimes(1);

		gate.resolve({ ok: true });
		await expect(first).resolves.toEqual({ ok: true });
		await expect(second).resolves.toEqual({ ok: true });
		expect(bridge.call).toHaveBeenCalledTimes(1);
	});

	it("re-fetches sequentially after the shared call settles (no stale cache)", async () => {
		const bridge = installPythonMock(() => Promise.resolve({ n: 1 }));
		const { result } = renderHook(() => usePython());

		await expect(result.current.call("get_config")).resolves.toEqual({
			n: 1,
		});
		await expect(result.current.call("get_config")).resolves.toEqual({
			n: 1,
		});
		expect(bridge.call).toHaveBeenCalledTimes(2);
	});

	it("does NOT share reads with different payloads", async () => {
		const bridge = installPythonMock(() => Promise.resolve([]));
		const { result } = renderHook(() => usePython());

		const first = result.current.call("get_history", { limit: 4 });
		const second = result.current.call("get_history", { limit: 5 });
		await expect(first).resolves.toEqual([]);
		await expect(second).resolves.toEqual([]);
		expect(bridge.call).toHaveBeenCalledTimes(2);
	});

	it("shares reads whose payload keys arrive in a different order", async () => {
		const gate = deferred<unknown>();
		const bridge = installPythonMock(() => gate.promise);
		const { result } = renderHook(() => usePython());

		const first = result.current.call("get_history", {
			limit: 5,
			offset: 0,
		});
		const second = result.current.call("get_history", {
			offset: 0,
			limit: 5,
		});
		expect(bridge.call).toHaveBeenCalledTimes(1);

		gate.resolve([]);
		await expect(first).resolves.toEqual([]);
		await expect(second).resolves.toEqual([]);
		expect(bridge.call).toHaveBeenCalledTimes(1);
	});

	it.each([["set_config"], ["toggle_dictation"]])(
		"does NOT share concurrent identical writes (%s)",
		async (type) => {
			const gate = deferred<unknown>();
			const bridge = installPythonMock(() => gate.promise);
			const { result } = renderHook(() => usePython());

			const first =
				type === "set_config"
					? result.current.call("set_config", { theme_mode: "dark" })
					: result.current.call("toggle_dictation");
			const second =
				type === "set_config"
					? result.current.call("set_config", { theme_mode: "dark" })
					: result.current.call("toggle_dictation");
			expect(bridge.call).toHaveBeenCalledTimes(2);

			gate.resolve({ ok: true });
			await expect(first).resolves.toEqual({ ok: true });
			await expect(second).resolves.toEqual({ ok: true });
			expect(bridge.call).toHaveBeenCalledTimes(2);
		},
	);

	it("propagates rejection to every sharer and clears so the next call retries", async () => {
		const firstGate = deferred<unknown>();
		const bridge = installPythonMock(() => firstGate.promise);
		const { result } = renderHook(() => usePython());

		const first = result.current.call("get_config");
		const second = result.current.call("get_config");
		expect(bridge.call).toHaveBeenCalledTimes(1);

		firstGate.reject(new Error("down"));
		await expect(first).rejects.toThrow("down");
		await expect(second).rejects.toThrow("down");

		bridge.call.mockImplementation(() => Promise.resolve({ ok: true }));
		await expect(result.current.call("get_config")).resolves.toEqual({
			ok: true,
		});
		expect(bridge.call).toHaveBeenCalledTimes(2);
	});
});
