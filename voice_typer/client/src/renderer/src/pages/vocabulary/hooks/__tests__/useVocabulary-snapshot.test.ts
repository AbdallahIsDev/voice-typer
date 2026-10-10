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

vi.mock("sonner", () => ({
	toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
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
import type { VocabularyData } from "@/types/ipc";
import {
	isVocabCacheFresh,
	peekVocabPrefetch,
	prefetchVocabularyData,
	useVocabulary,
	VOCAB_SNAPSHOT_TTL_MS,
} from "../useVocabulary";

const SNAPSHOT_KEY = "vocabulary.snapshot";

function vocabData(): VocabularyData {
	return {
		misspellings: { recieve: "receive" },
		phrase_corrections: [],
		extra_word_patterns: [],
		technical_terms: {},
		names: {},
		products: {},
	} as unknown as VocabularyData;
}

function usageSnapshot() {
	return {
		version: 1,
		entries: { misspellings: { recieve: { count: 3, last_ts: 7 } } },
	};
}

function stubFull(callMock: ReturnType<typeof vi.fn>) {
	callMock.mockImplementation((cmd: string) => {
		if (cmd === "get_vocabulary") return Promise.resolve(vocabData());
		if (cmd === "get_correction_usage") return Promise.resolve(usageSnapshot());
		if (cmd === "save_vocabulary") return Promise.resolve({ success: true });
		return Promise.resolve(null);
	});
}

function asPythonCall(mock: ReturnType<typeof vi.fn>): PythonCall {
	return mock as unknown as PythonCall;
}

function asPrefetchCall(mock: ReturnType<typeof vi.fn>): PrefetchCall {
	return mock as unknown as PrefetchCall;
}

function setup(callMock: ReturnType<typeof vi.fn>) {
	return renderHook(() =>
		useVocabulary({ call: asPythonCall(callMock), showSnack: vi.fn() }),
	);
}

describe("useVocabulary snapshot cache (C-CACHE-1/2)", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		cacheStore.clear();
		__resetPrefetchFlightsForTests();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.clearAllMocks();
	});

	it("revisit with a fresh snapshot renders entries + usage with no IPC", async () => {
		stubFull(callMock);
		const first = setup(callMock);
		await act(async () => {});
		expect(first.result.current.entries.map((e) => e.original)).toEqual([
			"recieve",
		]);
		expect(
			first.result.current.usageByKey.get("misspellings::recieve")?.count,
		).toBe(3);
		expect(isVocabCacheFresh()).toBe(true);
		first.unmount();
		callMock.mockClear();

		const second = setup(callMock);
		// First paint already shows entries AND usage counts.
		expect(second.result.current.entries.map((e) => e.original)).toEqual([
			"recieve",
		]);
		expect(
			second.result.current.usageByKey.get("misspellings::recieve")?.count,
		).toBe(3);
		await act(async () => {});
		expect(callMock.mock.calls.length).toBe(0);
		second.unmount();
	});

	it("stale snapshot revalidates in background while cached rows stay visible", async () => {
		stubFull(callMock);
		const first = setup(callMock);
		await act(async () => {});
		first.unmount();
		const snap = cacheStore.get(SNAPSHOT_KEY) as { fetchedAt: number };
		cacheStore.set(SNAPSHOT_KEY, {
			...(snap as unknown as Record<string, unknown>),
			fetchedAt: Date.now() - VOCAB_SNAPSHOT_TTL_MS - 1,
		});
		callMock.mockClear();

		const second = setup(callMock);
		expect(second.result.current.entries.map((e) => e.original)).toEqual([
			"recieve",
		]);
		await act(async () => {});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_vocabulary").length,
		).toBeGreaterThan(0);
		second.unmount();
	});

	it("re-races once after a dataless mount failure, then shows entries (cold-start storm)", async () => {
		vi.useFakeTimers();
		try {
			let attempts = 0;
			callMock.mockImplementation((cmd: string) => {
				if (cmd === "get_vocabulary") {
					attempts += 1;
					if (attempts === 1) return Promise.reject(new Error("boot storm"));
					return Promise.resolve(vocabData());
				}
				if (cmd === "get_correction_usage")
					return Promise.resolve(usageSnapshot());
				return Promise.resolve(null);
			});
			const hook = setup(callMock);
			await act(async () => {
				await vi.advanceTimersByTimeAsync(0);
			});

			expect(hook.result.current.entries).toHaveLength(0);
			expect(hook.result.current.loadError).not.toBeNull();

			await act(async () => {
				await vi.advanceTimersByTimeAsync(8000);
			});
			expect(hook.result.current.entries.map((e) => e.original)).toEqual([
				"recieve",
			]);
			expect(hook.result.current.loadError).toBeNull();

			await act(async () => {
				await vi.advanceTimersByTimeAsync(120_000);
			});
			expect(
				callMock.mock.calls.filter((c) => c[0] === "get_vocabulary"),
			).toHaveLength(2);
			hook.unmount();
		} finally {
			vi.useRealTimers();
		}
	});

	it("mount awaits an in-flight hover prefetch instead of duplicating it", async () => {
		stubFull(callMock);
		const pending = prefetchVocabularyData(asPrefetchCall(callMock));
		const hook = setup(callMock);
		await act(async () => {
			await pending;
		});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_vocabulary").length,
		).toBe(1);
		expect(hook.result.current.entries.map((e) => e.original)).toEqual([
			"recieve",
		]);
		hook.unmount();
	});
});

describe("prefetchVocabularyData (C-CACHE-4)", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		cacheStore.clear();
		__resetPrefetchFlightsForTests();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.clearAllMocks();
	});

	it("warms the snapshot; repeated hovers issue no further IPC", async () => {
		stubFull(callMock);
		await prefetchVocabularyData(asPrefetchCall(callMock));
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_vocabulary").length,
		).toBe(1);
		callMock.mockClear();
		await prefetchVocabularyData(asPrefetchCall(callMock));
		await prefetchVocabularyData(asPrefetchCall(callMock));
		expect(callMock.mock.calls.length).toBe(0);
		expect(peekVocabPrefetch()).toBeNull();
	});

	it("concurrent hovers share one flight", async () => {
		let resolveVocab!: (v: unknown) => void;
		const gate = new Promise<unknown>((resolve) => {
			resolveVocab = resolve;
		});
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_vocabulary") return gate;
			if (cmd === "get_correction_usage")
				return Promise.resolve(usageSnapshot());
			return Promise.resolve(null);
		});
		const p1 = prefetchVocabularyData(asPrefetchCall(callMock));
		const p2 = prefetchVocabularyData(asPrefetchCall(callMock));
		resolveVocab(vocabData());
		await p1;
		await p2;
		expect(
			callMock.mock.calls.filter((c) => c[0] === "get_vocabulary").length,
		).toBe(1);
	});

	it("failures and error envelopes leave the cache empty", async () => {
		callMock.mockRejectedValue(new Error("backend down"));
		await prefetchVocabularyData(asPrefetchCall(callMock));
		expect(cacheStore.has(SNAPSHOT_KEY)).toBe(false);

		__resetPrefetchFlightsForTests();
		callMock.mockReset();
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_vocabulary")
				return Promise.resolve({ type: "error", data: {} });
			if (cmd === "get_correction_usage")
				return Promise.resolve(usageSnapshot());
			return Promise.resolve(null);
		});
		await prefetchVocabularyData(asPrefetchCall(callMock));
		expect(cacheStore.has(SNAPSHOT_KEY)).toBe(false);
	});

	it("saves rewrite the snapshot with exactly what was saved", async () => {
		stubFull(callMock);
		const hook = setup(callMock);
		await act(async () => {});
		const saved = [
			...hook.result.current.entries,
			{
				_id: "teh",
				original: "teh",
				correction: "the",
				category: "misspellings",
			} as const,
		];
		await act(async () => {
			await hook.result.current.persistVocabulary([...saved]);
		});
		const snap = cacheStore.get(SNAPSHOT_KEY) as {
			entries: { original: string }[];
		};
		expect(snap.entries.map((e) => e.original).sort()).toEqual([
			"recieve",
			"teh",
		]);
		hook.unmount();
	});
});
