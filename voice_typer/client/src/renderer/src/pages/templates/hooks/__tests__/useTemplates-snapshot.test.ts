import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { cacheStore } = vi.hoisted(() => ({
	cacheStore: new Map<string, unknown>(),
}));

vi.mock("@/lib/ipcCache", () => ({
	peekIpcCache: (key: string) => cacheStore.get(key),
	writeIpcCache: vi.fn((key: string, value: unknown) => {
		cacheStore.set(key, value);
	}),
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
	getLocale: () => "en",
}));

import type { PythonCall } from "@/hooks/usePython";
import {
	__resetPrefetchFlightsForTests,
	type PrefetchCall,
} from "@/lib/snapshotCache";
import {
	isTemplatesCacheFresh,
	peekTemplatesPrefetch,
	prefetchTemplatesData,
	TEMPLATES_SNAPSHOT_TTL_MS,
	useTemplates,
} from "../useTemplates";

const SNAPSHOT_KEY = "templates.snapshot";

function stubRows(callMock: ReturnType<typeof vi.fn>) {
	callMock.mockImplementation((cmd: string) => {
		if (cmd === "get_templates")
			return Promise.resolve({
				templates: [
					{ trigger: "brb", output: "be right back", match_mode: "exact" },
				],
			});
		return Promise.resolve(null);
	});
}

function asPrefetchCall(mock: ReturnType<typeof vi.fn>): PrefetchCall {
	return mock as unknown as PrefetchCall;
}

function setup(callMock: ReturnType<typeof vi.fn>) {
	return renderHook(() =>
		useTemplates({
			call: callMock as unknown as PythonCall,
			showSnack: vi.fn(),
			markUpdated: vi.fn(),
		}),
	);
}

describe("useTemplates snapshot cache (C-CACHE-2)", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		cacheStore.clear();
		__resetPrefetchFlightsForTests();
		localStorage.clear();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.clearAllMocks();
	});

	it("revisit with a fresh snapshot renders rows with no IPC", async () => {
		stubRows(callMock);
		const first = setup(callMock);
		await act(async () => {});
		expect(first.result.current.templates.map((t) => t.trigger)).toEqual([
			"brb",
		]);
		expect(isTemplatesCacheFresh()).toBe(true);
		first.unmount();
		callMock.mockClear();

		const second = setup(callMock);
		expect(second.result.current.templates.map((t) => t.trigger)).toEqual([
			"brb",
		]);
		await act(async () => {});
		expect(callMock.mock.calls.length).toBe(0);
		second.unmount();
	});

	it("stale snapshot revalidates in background while cached rows stay visible", async () => {
		stubRows(callMock);
		const first = setup(callMock);
		await act(async () => {});
		first.unmount();
		const snap = cacheStore.get(SNAPSHOT_KEY) as { fetchedAt: number };
		cacheStore.set(SNAPSHOT_KEY, {
			...(snap as unknown as Record<string, unknown>),
			fetchedAt: Date.now() - TEMPLATES_SNAPSHOT_TTL_MS - 1,
		});
		callMock.mockClear();

		const second = setup(callMock);
		expect(second.result.current.templates.map((t) => t.trigger)).toEqual([
			"brb",
		]);
		await act(async () => {});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_templates").length,
		).toBeGreaterThan(0);
		second.unmount();
	});

	it("re-races once after a dataless mount failure, then shows rows (cold-start storm)", async () => {
		vi.useFakeTimers();
		try {
			let attempts = 0;
			callMock.mockImplementation((cmd: string) => {
				if (cmd === "get_templates") {
					attempts += 1;
					if (attempts === 1) return Promise.reject(new Error("boot storm"));
					return Promise.resolve({
						templates: [
							{
								trigger: "brb",
								output: "be right back",
								match_mode: "exact",
							},
						],
					});
				}
				return Promise.resolve(null);
			});
			const hook = setup(callMock);
			await act(async () => {
				await vi.advanceTimersByTimeAsync(0);
			});

			expect(hook.result.current.templates).toHaveLength(0);
			expect(hook.result.current.loadError).not.toBeNull();

			await act(async () => {
				await vi.advanceTimersByTimeAsync(8000);
			});
			expect(hook.result.current.templates.map((t) => t.trigger)).toEqual([
				"brb",
			]);
			expect(hook.result.current.loadError).toBeNull();

			await act(async () => {
				await vi.advanceTimersByTimeAsync(120_000);
			});
			expect(
				callMock.mock.calls.filter((c) => c[0] === "get_templates"),
			).toHaveLength(2);
			hook.unmount();
		} finally {
			vi.useRealTimers();
		}
	});

	it("mount awaits an in-flight hover prefetch instead of duplicating it", async () => {
		stubRows(callMock);
		const pending = prefetchTemplatesData(asPrefetchCall(callMock));
		const hook = setup(callMock);
		await act(async () => {
			await pending;
		});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_templates").length,
		).toBe(1);
		expect(hook.result.current.templates.map((t) => t.trigger)).toEqual([
			"brb",
		]);
		hook.unmount();
	});
});

describe("prefetchTemplatesData (C-CACHE-4)", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		cacheStore.clear();
		__resetPrefetchFlightsForTests();
		localStorage.clear();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.clearAllMocks();
	});

	it("warms the snapshot; repeated hovers issue no further IPC", async () => {
		stubRows(callMock);
		await prefetchTemplatesData(asPrefetchCall(callMock));
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_templates").length,
		).toBe(1);
		callMock.mockClear();
		await prefetchTemplatesData(asPrefetchCall(callMock));
		await prefetchTemplatesData(asPrefetchCall(callMock));
		expect(callMock.mock.calls.length).toBe(0);
		expect(peekTemplatesPrefetch()).toBeNull();
	});

	it("malformed payloads leave the cache empty (mount stays authoritative)", async () => {
		callMock.mockResolvedValue({ nope: true });
		await prefetchTemplatesData(asPrefetchCall(callMock));
		expect(cacheStore.has(SNAPSHOT_KEY)).toBe(false);
	});
});
