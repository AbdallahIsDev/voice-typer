import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { usePythonEventMock } = vi.hoisted(() => ({
	usePythonEventMock: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: usePythonEventMock,
}));

vi.mock("sonner", () => ({
	toast: { error: vi.fn() },
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string) => key,
	tChoice: (key: string) => key,
	getLocale: () => "en",
}));

const { cacheStore } = vi.hoisted(() => ({
	cacheStore: new Map<string, unknown>(),
}));

vi.mock("@/lib/ipcCache", () => ({
	peekIpcCache: (key: string) => cacheStore.get(key),
	writeIpcCache: vi.fn((key: string, value: unknown) => {
		cacheStore.set(key, value);
	}),
}));

import { useAnalyticsRange } from "@/stores/useAnalyticsRange";
import type { LausuConfig } from "@/types/config";
import type { HistoryRecord, ModelStatusMap } from "@/types/ipc";
import type { CorrectionUsageSnapshot } from "../../lib/streaks";
import {
	DASHBOARD_CACHE_TTL_MS,
	DASHBOARD_DELTA_LIMIT,
	DASHBOARD_SAMPLE_LIMIT,
	type PrefetchCall,
	prefetchDashboardData,
	useDashboardData,
} from "../useDashboardData";

const SNAPSHOT_KEY = "analytics.dashboardSnapshot";

function stubFull(
	callMock: ReturnType<typeof vi.fn>,
	rows: HistoryRecord[],
	count: number,
) {
	callMock.mockImplementation((cmd: string) => {
		if (cmd === "get_config") return Promise.resolve(makeConfig());
		if (cmd === "get_history") return Promise.resolve(rows);
		if (cmd === "get_history_count") return Promise.resolve({ count });
		if (cmd === "get_correction_usage") return Promise.resolve(null);
		if (cmd === "get_model_status") return Promise.resolve({});
		return Promise.resolve(null);
	});
}

type CallStub = <T = unknown>(
	type: string,
	data?: Record<string, unknown>,
) => Promise<T>;

function asCallStub(mock: ReturnType<typeof vi.fn>): CallStub {
	return mock as unknown as CallStub;
}

function makeRow(
	id: number,
	overrides: Partial<HistoryRecord> = {},
): HistoryRecord {
	return {
		id,
		text: `dictation ${id}`,
		timestamp: new Date().toISOString(),
		duration: 5,
		model: "tiny",
		device: "cpu",
		word_count: 2,
		char_count: 11,
		favorite: 0,
		language: "en",
		...overrides,
	};
}

function makeConfig(): LausuConfig {
	return {
		model_size: "tiny",
		device: "cpu",
		language: "en",
	} as LausuConfig;
}

function localTodayKey(): string {
	const d = new Date();
	const pad = (n: number) => String(n).padStart(2, "0");
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Latest handler captured for an event type. */
function getEventHandler(type: string) {
	const calls = usePythonEventMock.mock.calls.filter((c) => c[0] === type);
	const last = calls[calls.length - 1];
	return last?.[1] as (() => (() => void) | undefined) | undefined;
}

function callsOf(callMock: ReturnType<typeof vi.fn>, cmd: string) {
	return callMock.mock.calls.filter((c) => c[0] === cmd);
}

describe("useDashboardData hot/cold split", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.useFakeTimers();
		Object.defineProperty(document, "visibilityState", {
			value: "visible",
			configurable: true,
		});
		usePythonEventMock.mockClear();
		cacheStore.clear();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.clearAllMocks();
	});

	/** Mount with a 2-row history; resolves once data is set. */
	async function mountWith(rows: HistoryRecord[], count: number) {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_history") return Promise.resolve(rows);
			if (cmd === "get_history_count") return Promise.resolve({ count });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status") return Promise.resolve({});
			return Promise.resolve(null);
		});
		const hook = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		return hook;
	}

	async function fireEvent(type: string) {
		await act(async () => {
			getEventHandler(type)?.();
			await vi.advanceTimersByTimeAsync(600);
		});
	}

	it("mount performs the full refresh (five IPCs, 500-row sample)", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const { result } = await mountWith(rows, 2);

		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_history_count").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_correction_usage").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_model_status").length).toBeGreaterThan(0);
		const historyCalls = callsOf(callMock, "get_history");
		expect(historyCalls.length).toBeGreaterThan(0);
		expect(historyCalls[0]?.[1]).toEqual({ limit: DASHBOARD_SAMPLE_LIMIT });
		expect(result.current.data?.totalCount).toBe(2);
		expect(result.current.data?.sampleSize).toBe(2);
	});

	it("transcription_final with count+1 applies the delta (no cold IPCs)", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const { result } = await mountWith(rows, 2);
		expect(result.current.data?.totalCount).toBe(2);

		const fresh = makeRow(3);
		callMock.mockClear();
		callMock.mockImplementation(
			(cmd: string, data?: Record<string, unknown>) => {
				if (cmd === "get_history") {
					expect(data).toEqual({ limit: DASHBOARD_DELTA_LIMIT });
					return Promise.resolve([fresh]);
				}
				if (cmd === "get_history_count") return Promise.resolve({ count: 3 });
				if (cmd === "get_correction_usage") return Promise.resolve(null);
				return Promise.reject(new Error(`unexpected cold IPC: ${cmd}`));
			},
		);

		await fireEvent("transcription_final");

		// Delta fetched the 10-row head, never the cold getters.
		expect(callsOf(callMock, "get_history").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_config")).toHaveLength(0);
		expect(callsOf(callMock, "get_model_status")).toHaveLength(0);
		// New row prepended, count bumped, cold-derived fields intact.
		expect(result.current.data?.totalCount).toBe(3);
		expect(result.current.data?.sampleSize).toBe(3);
		expect(result.current.data?.todayCount).toBe(3);
	});

	it("count jump (+2) falls back to the full refresh", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const { result } = await mountWith(rows, 2);

		const full = [makeRow(4), makeRow(3), makeRow(2), makeRow(1)];
		callMock.mockClear();
		callMock.mockImplementation(
			(cmd: string, data?: Record<string, unknown>) => {
				if (cmd === "get_history") {
					if ((data?.limit as number) === DASHBOARD_DELTA_LIMIT)
						return Promise.resolve([makeRow(4)]);
					return Promise.resolve(full);
				}
				if (cmd === "get_history_count") return Promise.resolve({ count: 4 });
				if (cmd === "get_correction_usage") return Promise.resolve(null);
				if (cmd === "get_config") return Promise.resolve(makeConfig());
				if (cmd === "get_model_status")
					return Promise.resolve({} as ModelStatusMap);
				return Promise.resolve(null);
			},
		);

		await fireEvent("history_changed");

		// Fallback re-ran the full path (cold getters + 500-row sample).
		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
		const historyCalls = callsOf(callMock, "get_history");
		expect(
			historyCalls.some((c) => (c[1] as { limit: number })?.limit === 500),
		).toBe(true);
		expect(result.current.data?.totalCount).toBe(4);
		expect(result.current.data?.sampleSize).toBe(4);
	});

	it("empty delta head falls back to the full refresh", async () => {
		const rows = [makeRow(2), makeRow(1)];
		await mountWith(rows, 2);

		callMock.mockClear();
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_history") return Promise.resolve([]);
			if (cmd === "get_history_count") return Promise.resolve({ count: 3 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_model_status")
				return Promise.resolve({} as ModelStatusMap);
			return Promise.resolve(null);
		});

		await fireEvent("transcription_final");

		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
	});

	it("duplicate head id falls back to the full refresh", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const { result } = await mountWith(rows, 2);

		callMock.mockClear();
		callMock.mockImplementation((cmd: string) => {
			// Backend re-delivers the already-known head row.
			if (cmd === "get_history") return Promise.resolve([makeRow(2)]);
			if (cmd === "get_history_count") return Promise.resolve({ count: 3 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_model_status")
				return Promise.resolve({} as ModelStatusMap);
			return Promise.resolve(null);
		});

		await fireEvent("transcription_final");

		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
		// Full path re-synced authoritatively (count 3, sample from server).
		expect(result.current.data?.totalCount).toBe(3);
	});

	it("config_changed triggers the full refresh (cold getters re-fire)", async () => {
		const rows = [makeRow(2), makeRow(1)];
		await mountWith(rows, 2);

		callMock.mockClear();
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_history") return Promise.resolve(rows);
			if (cmd === "get_history_count") return Promise.resolve({ count: 2 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status")
				return Promise.resolve({} as ModelStatusMap);
			return Promise.resolve(null);
		});

		await fireEvent("config_changed");

		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
		// The status leg rides the shared model-status snapshot: the
		// mount just wrote it fresh, so no disk re-stat here (that is
		// the C-CACHE-5 optimization, not a skipped refresh — config,
		// history, and corrections below all re-fired).
		const historyCalls = callsOf(callMock, "get_history");
		expect(historyCalls.length).toBeGreaterThan(0);
		// Full path only, no 10-row delta fetch.
		for (const c of historyCalls) {
			expect((c[1] as { limit: number })?.limit).toBe(DASHBOARD_SAMPLE_LIMIT);
		}
	});

	it("corrections card updates from the hot path alone", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const { result } = await mountWith(rows, 2);
		expect(result.current.correctionStats.corrections).toBe(0);

		const snapshot: CorrectionUsageSnapshot = {
			version: 1,
			entries: {},
			corrections_by_day: { [localTodayKey()]: 2 },
			dictations_by_day: { [localTodayKey()]: 1 },
		};
		callMock.mockClear();
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_history") return Promise.resolve([makeRow(3)]);
			if (cmd === "get_history_count") return Promise.resolve({ count: 3 });
			if (cmd === "get_correction_usage") return Promise.resolve(snapshot);
			return Promise.reject(new Error(`unexpected cold IPC: ${cmd}`));
		});

		await fireEvent("transcription_final");

		expect(callsOf(callMock, "get_config")).toHaveLength(0);
		expect(result.current.correctionStats.corrections).toBe(2);
		expect(result.current.data?.totalCount).toBe(3);
	});
});

describe("useDashboardData custom windows", () => {
	let callMock: ReturnType<typeof vi.fn>;

	function dayKey(daysAgo = 0): string {
		const d = new Date();
		d.setDate(d.getDate() - daysAgo);
		const pad = (n: number) => String(n).padStart(2, "0");
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
	}

	function rowOnDay(id: number, daysAgo: number): HistoryRecord {
		const d = new Date();
		d.setDate(d.getDate() - daysAgo);
		d.setHours(12, 0, 0, 0);
		return makeRow(id, { timestamp: d.toISOString() });
	}

	function windowCalls() {
		return callsOf(callMock, "get_history").filter(
			(c) => (c[1] as Record<string, unknown>)?.start_ts !== undefined,
		);
	}

	beforeEach(() => {
		vi.useFakeTimers();
		Object.defineProperty(document, "visibilityState", {
			value: "visible",
			configurable: true,
		});
		sessionStorage.clear();
		useAnalyticsRange.setState({ range: "7d", customWindow: null });
		cacheStore.clear();
		callMock = vi.fn((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_history_count") return Promise.resolve({ count: 0 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status") return Promise.resolve({});
			return Promise.resolve(null);
		});
	});

	afterEach(() => {
		useAnalyticsRange.setState({ range: "7d", customWindow: null });
		sessionStorage.clear();
		vi.useRealTimers();
		vi.clearAllMocks();
	});

	async function mountCustom() {
		useAnalyticsRange
			.getState()
			.setCustomRange({ startKey: dayKey(6), endKey: dayKey(0) });
		const hook = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(50);
		});
		await act(async () => {
			await vi.advanceTimersByTimeAsync(50);
		});
		return hook;
	}

	it("fetches the window with UTC bounds and scopes stats to it", async () => {
		const windowRows = [rowOnDay(3, 6), rowOnDay(2, 5), rowOnDay(1, 4)];
		callMock.mockImplementation(
			(cmd: string, data?: Record<string, unknown>) => {
				if (cmd === "get_config") return Promise.resolve(makeConfig());
				if (cmd === "get_history") {
					if (data?.start_ts !== undefined) return Promise.resolve(windowRows);
					return Promise.resolve([]);
				}
				if (cmd === "get_history_count") return Promise.resolve({ count: 3 });
				if (cmd === "get_correction_usage") return Promise.resolve(null);
				if (cmd === "get_model_status") return Promise.resolve({});
				return Promise.resolve(null);
			},
		);
		const { result } = await mountCustom();

		const calls = windowCalls();
		expect(calls.length).toBeGreaterThan(0);
		const sent = calls[0]?.[1] as Record<string, string>;
		expect(sent.start_ts).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
		expect(sent.end_ts).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
		expect(result.current.period.count).toBe(3);
		expect(result.current.customReady).toBe(true);
		expect(result.current.customCapped).toBe(false);
		expect(result.current.customWindowLabel).toContain("–");
	});

	it("pages with the cursor and marks the cap after ten full pages", async () => {
		// 5000 rows spread across the 7-day window (each page full, so
		// the loop runs the whole 10-page budget and marks sampled).
		const page = (base: number) =>
			Array.from({ length: 500 }, (_, i) => rowOnDay(base + i, (base + i) % 7));
		let pages = 0;
		callMock.mockImplementation(
			(cmd: string, data?: Record<string, unknown>) => {
				if (cmd === "get_config") return Promise.resolve(makeConfig());
				if (cmd === "get_history") {
					if (data?.start_ts === undefined) return Promise.resolve([]);
					pages += 1;
					if (pages === 1) return Promise.resolve(page(1));
					// Second page proves cursor forwarding, then stay full.
					return Promise.resolve(page(1000 + pages));
				}
				if (cmd === "get_history_count")
					return Promise.resolve({ count: 99999 });
				if (cmd === "get_correction_usage") return Promise.resolve(null);
				if (cmd === "get_model_status") return Promise.resolve({});
				return Promise.resolve(null);
			},
		);
		const { result } = await mountCustom();

		const calls = windowCalls();
		expect(calls).toHaveLength(10);
		const first = calls[0]?.[1] as Record<string, unknown> | undefined;
		const second = calls[1]?.[1] as Record<string, unknown> | undefined;
		expect(typeof second?.before_timestamp).toBe("string");
		expect(typeof second?.before_id).toBe("number");
		expect(second?.start_ts).toBe(first?.start_ts);
		expect(result.current.customCapped).toBe(true);
		expect(result.current.period.count).toBe(5000);
	});
});

describe("useDashboardData cold-start mount retry", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.useFakeTimers();
		Object.defineProperty(document, "visibilityState", {
			value: "visible",
			configurable: true,
		});
		usePythonEventMock.mockClear();
		cacheStore.clear();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.clearAllMocks();
	});

	it("re-races once after a dataless mount failure, then shows data", async () => {
		let attempts = 0;
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") {
				attempts += 1;
				if (attempts === 1) return Promise.reject(new Error("startup storm"));
				return Promise.resolve(makeConfig());
			}
			if (cmd === "get_history") return Promise.resolve([makeRow(1)]);
			if (cmd === "get_history_count") return Promise.resolve({ count: 1 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status") return Promise.resolve({});
			return Promise.resolve(null);
		});
		const { result } = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});

		// First attempt failed: the error screen owns the page for now.
		expect(result.current.data).toBeNull();
		expect(result.current.fetchError).not.toBeNull();

		// The single delayed re-race heals it without manual Retry.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(8000);
		});
		expect(callsOf(callMock, "get_config")).toHaveLength(2);
		expect(result.current.data?.totalCount).toBe(1);
		expect(result.current.fetchError).toBeNull();
	});

	it("keeps the error screen (bounded: no third attempt) when the retry fails", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config")
				return Promise.reject(new Error("backend down"));
			if (cmd === "get_history") return Promise.resolve([]);
			if (cmd === "get_history_count") return Promise.resolve({ count: 0 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status") return Promise.resolve({});
			return Promise.resolve(null);
		});
		const { result } = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(result.current.data).toBeNull();

		await act(async () => {
			await vi.advanceTimersByTimeAsync(8000);
		});
		expect(callsOf(callMock, "get_config")).toHaveLength(2);
		expect(result.current.fetchError).not.toBeNull();

		// No runaway loop: far-future timers add no further attempts.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(120_000);
		});
		expect(callsOf(callMock, "get_config")).toHaveLength(2);
	});
});

describe("useDashboardData snapshot cache (C-CACHE-1/2)", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.useFakeTimers();
		Object.defineProperty(document, "visibilityState", {
			value: "visible",
			configurable: true,
		});
		usePythonEventMock.mockClear();
		cacheStore.clear();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.clearAllMocks();
	});

	async function mountWithRows(rows: HistoryRecord[], count: number) {
		stubFull(callMock, rows, count);
		const hook = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		return hook;
	}

	it("revisit with a fresh snapshot renders cached stats with no IPC", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const first = await mountWithRows(rows, 2);
		expect(first.result.current.data?.totalCount).toBe(2);
		first.unmount();
		callMock.mockClear();

		const second = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		// First paint already shows cached data AND derived stats: no
		// zero placeholders, no skeleton.
		expect(second.result.current.data?.totalCount).toBe(2);
		expect(second.result.current.period.count).toBe(2);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(callMock.mock.calls.length).toBe(0);
		second.unmount();
	});

	it("stale snapshot revalidates in background while cached stats stay visible", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const first = await mountWithRows(rows, 2);
		first.unmount();
		const snap = cacheStore.get(SNAPSHOT_KEY) as { fetchedAt: number };
		cacheStore.set(SNAPSHOT_KEY, {
			...(snap as unknown as Record<string, unknown>),
			fetchedAt: Date.now() - DASHBOARD_CACHE_TTL_MS - 1,
		});
		callMock.mockClear();
		stubFull(callMock, rows, 2);

		const second = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		// Cached stats stay on screen while the refresh runs.
		expect(second.result.current.data?.totalCount).toBe(2);
		expect(second.result.current.period.count).toBe(2);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(callsOf(callMock, "get_history").length).toBeGreaterThan(0);
		expect(second.result.current.data?.totalCount).toBe(2);
		second.unmount();
	});

	it("slow failures skip the re-race (bridge patience already applied, C-CACHE-10)", async () => {
		let rejectLoad!: (err: Error) => void;
		const gate = new Promise<LausuConfig>((_, reject) => {
			rejectLoad = reject;
		});
		callMock.mockImplementation((cmd: string) => {
			// The gating call hangs 35s (bridge waited), then fails.
			if (cmd === "get_config") return gate;
			if (cmd === "get_history") return Promise.resolve([]);
			if (cmd === "get_history_count") return Promise.resolve({ count: 0 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status") return Promise.resolve({});
			return Promise.resolve(null);
		});
		const hook = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(35_000);
			rejectLoad(new Error("boot storm"));
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(hook.result.current.data).toBeNull();
		expect(hook.result.current.fetchError).not.toBeNull();
		const configs = callsOf(callMock, "get_config").length;
		// No second wait stacked: straight to the error screen.
		await act(async () => {
			await vi.advanceTimersByTimeAsync(120_000);
		});
		expect(callsOf(callMock, "get_config")).toHaveLength(configs);
		hook.unmount();
	});

	it("mount awaits an in-flight hover prefetch instead of duplicating it", async () => {
		const rows = [makeRow(2), makeRow(1)];
		stubFull(callMock, rows, 2);
		const prefetchCall = callMock as unknown as PrefetchCall;
		const pending = prefetchDashboardData(prefetchCall);
		const hook = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		await act(async () => {
			await pending;
			await vi.advanceTimersByTimeAsync(0);
		});
		// One set of IPCs total: the mount piggybacked the prefetch.
		expect(callsOf(callMock, "get_history")).toHaveLength(1);
		expect(hook.result.current.data?.totalCount).toBe(2);
		hook.unmount();
	});
});

describe("prefetchDashboardData (C-CACHE-4)", () => {
	let callMock: ReturnType<typeof vi.fn>;

	beforeEach(() => {
		vi.useFakeTimers();
		cacheStore.clear();
		callMock = vi.fn();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.clearAllMocks();
	});

	function prefetchCall(): PrefetchCall {
		return callMock as unknown as PrefetchCall;
	}

	it("warms the snapshot so the next mount skips its fetch", async () => {
		stubFull(callMock, [makeRow(2), makeRow(1)], 2);
		await prefetchDashboardData(prefetchCall());
		expect(callsOf(callMock, "get_history")).toHaveLength(1);
		callMock.mockClear();

		const hook = renderHook(() =>
			useDashboardData({ call: asCallStub(callMock) }),
		);
		expect(hook.result.current.data?.totalCount).toBe(2);
		await act(async () => {
			await vi.advanceTimersByTimeAsync(0);
		});
		expect(callMock.mock.calls.length).toBe(0);
		hook.unmount();
	});

	it("repeated hovers issue no further IPC while the snapshot is fresh", async () => {
		stubFull(callMock, [makeRow(1)], 1);
		await prefetchDashboardData(prefetchCall());
		callMock.mockClear();
		await prefetchDashboardData(prefetchCall());
		await prefetchDashboardData(prefetchCall());
		expect(callMock.mock.calls.length).toBe(0);
	});

	it("concurrent hovers share one flight", async () => {
		let resolveHistory!: (rows: HistoryRecord[]) => void;
		const gate = new Promise<HistoryRecord[]>((resolve) => {
			resolveHistory = resolve;
		});
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			if (cmd === "get_history") return gate;
			if (cmd === "get_history_count") return Promise.resolve({ count: 1 });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status") return Promise.resolve({});
			return Promise.resolve(null);
		});
		const p1 = prefetchDashboardData(prefetchCall());
		const p2 = prefetchDashboardData(prefetchCall());
		resolveHistory([makeRow(1)]);
		await p1;
		await p2;
		expect(callsOf(callMock, "get_history")).toHaveLength(1);
	});

	it("failures leave the cache empty (mount stays authoritative)", async () => {
		callMock.mockRejectedValue(new Error("backend down"));
		await prefetchDashboardData(prefetchCall());
		expect(cacheStore.has(SNAPSHOT_KEY)).toBe(false);
	});
});
