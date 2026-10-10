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

import {
	__resetPrefetchFlightsForTests,
	type PrefetchCall,
} from "@/lib/snapshotCache";
import type { ModelStatusMap, ModelStatusResponse } from "@/types/ipc";
import {
	expireModelStatusSnapshot,
	fetchSharedModelStatus,
	isModelStatusFresh,
	isModelStatusResponse,
	MODEL_STATUS_SNAPSHOT_KEY,
	MODEL_STATUS_TTL_MS,
	patchCachedModelDownloaded,
	peekModelStatusFlight,
	prefetchModelsStatus,
	readModelStatusSnapshot,
	writeModelStatusSnapshot,
} from "../modelStatusCache";

function asCall(mock: ReturnType<typeof vi.fn>): PrefetchCall {
	return mock as unknown as PrefetchCall;
}

function validStatus(): ModelStatusResponse {
	// NOTE: the index signature on ModelStatusMap rejects a literal
	// `_storage` key, so the response is assembled via spread (the
	// backend builds it the same way: map first, summary attached).
	const map: ModelStatusMap = {
		tiny: { downloaded: true, deps_ok: true },
		"large-v3-turbo": { downloaded: false, deps_ok: true },
	};
	return {
		...map,
		_storage: { used_bytes: 10, hub_path: "h", config_dir: "c" },
	} as ModelStatusResponse;
}

beforeEach(() => {
	cacheStore.clear();
	__resetPrefetchFlightsForTests();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("isModelStatusResponse", () => {
	it("accepts a valid map, an empty map, and one with _storage", () => {
		expect(isModelStatusResponse(validStatus())).toBe(true);
		expect(isModelStatusResponse({})).toBe(true);
		expect(
			isModelStatusResponse({
				_storage: { used_bytes: 1, hub_path: "h", config_dir: "c" },
			}),
		).toBe(true);
	});

	it("rejects nulls, arrays, envelopes, and crossed shapes", () => {
		for (const bad of [
			null,
			undefined,
			"ok",
			42,
			[],
			[{ downloaded: true }],
			{ type: "error", data: {} },
			{ _error: "backend down" },
			{ count: 1 },
			{ tiny: { downloaded: "yes" } },
			{ tiny: null },
		]) {
			expect(isModelStatusResponse(bad)).toBe(false);
		}
	});
});

describe("fetchSharedModelStatus", () => {
	it("miss fetches, writes, and a fresh revisit issues no IPC", async () => {
		const callMock = vi.fn(async () => validStatus());
		const first = await fetchSharedModelStatus(asCall(callMock));
		expect(first?.tiny?.downloaded).toBe(true);
		expect(callMock).toHaveBeenCalledTimes(1);
		expect(isModelStatusFresh()).toBe(true);

		callMock.mockClear();
		const second = await fetchSharedModelStatus(asCall(callMock));
		expect(second?.tiny?.downloaded).toBe(true);
		expect(callMock).not.toHaveBeenCalled();
	});

	it("concurrent callers share one flight", async () => {
		let resolveStatus!: (v: unknown) => void;
		const gate = new Promise<unknown>((resolve) => {
			resolveStatus = resolve;
		});
		const callMock = vi.fn(() => gate);
		const p1 = fetchSharedModelStatus(asCall(callMock));
		const p2 = fetchSharedModelStatus(asCall(callMock));
		expect(peekModelStatusFlight()).not.toBeNull();
		resolveStatus(validStatus());
		const [r1, r2] = await Promise.all([p1, p2]);
		expect(callMock).toHaveBeenCalledTimes(1);
		expect(r1?.tiny?.downloaded).toBe(true);
		expect(r2?.tiny?.downloaded).toBe(true);
		expect(peekModelStatusFlight()).toBeNull();
	});

	it("failure with no cache resolves null and writes nothing", async () => {
		const callMock = vi.fn(async () => {
			throw new Error("backend down");
		});
		await expect(fetchSharedModelStatus(asCall(callMock))).resolves.toBeNull();
		expect(cacheStore.has(MODEL_STATUS_SNAPSHOT_KEY)).toBe(false);
	});

	it("failure with a stale cache serves the stale snapshot without rewriting it", async () => {
		const callMock = vi.fn(async () => validStatus());
		await fetchSharedModelStatus(asCall(callMock));
		const snap = readModelStatusSnapshot();
		if (snap) {
			cacheStore.set(MODEL_STATUS_SNAPSHOT_KEY, {
				...snap,
				fetchedAt: Date.now() - MODEL_STATUS_TTL_MS - 1,
			});
		}
		callMock.mockImplementation(async () => {
			throw new Error("backend down");
		});
		const out = await fetchSharedModelStatus(asCall(callMock));
		expect(out?.tiny?.downloaded).toBe(true);
		const after = readModelStatusSnapshot();
		expect(after && Date.now() - after.fetchedAt).toBeGreaterThan(
			MODEL_STATUS_TTL_MS,
		);
	});

	it("invalid payloads are never written", async () => {
		const callMock = vi.fn(async () => ({ type: "error", data: {} }));
		await expect(fetchSharedModelStatus(asCall(callMock))).resolves.toBeNull();
		expect(cacheStore.has(MODEL_STATUS_SNAPSHOT_KEY)).toBe(false);
	});
});

describe("patchCachedModelDownloaded + expireModelStatusSnapshot", () => {
	it("a confirmed delete clears the flag and renews the snapshot", () => {
		const seeded: ModelStatusResponse = {
			tiny: { downloaded: true, deps_ok: true },
			"large-v3-turbo": { downloaded: true, deps_ok: true },
		};
		writeModelStatusSnapshot(seeded);
		patchCachedModelDownloaded("tiny", false);
		const snap = readModelStatusSnapshot();
		expect(snap?.status.tiny?.downloaded).toBe(false);
		// Sibling entries untouched.
		expect(snap?.status["large-v3-turbo"]?.downloaded).toBe(true);
		expect(isModelStatusFresh()).toBe(true);
	});

	it("never invents downloaded:true and no-ops without a snapshot", () => {
		writeModelStatusSnapshot(validStatus());
		patchCachedModelDownloaded("tiny", true);
		expect(readModelStatusSnapshot()?.status.tiny?.downloaded).toBe(true);
		cacheStore.clear();
		expect(() => patchCachedModelDownloaded("tiny", false)).not.toThrow();
		expect(cacheStore.has(MODEL_STATUS_SNAPSHOT_KEY)).toBe(false);
	});

	it("expire forces the next reader to re-stat", () => {
		writeModelStatusSnapshot(validStatus());
		expect(isModelStatusFresh()).toBe(true);
		expireModelStatusSnapshot();
		expect(isModelStatusFresh()).toBe(false);
		// Expiring an empty cache is a no-op.
		cacheStore.clear();
		expect(() => expireModelStatusSnapshot()).not.toThrow();
	});
});

describe("prefetchModelsStatus", () => {
	it("warms the snapshot; repeated hovers issue no further IPC", async () => {
		const callMock = vi.fn(async () => validStatus());
		await prefetchModelsStatus(asCall(callMock));
		expect(callMock).toHaveBeenCalledTimes(1);
		callMock.mockClear();
		await prefetchModelsStatus(asCall(callMock));
		expect(callMock).not.toHaveBeenCalled();
	});
});
