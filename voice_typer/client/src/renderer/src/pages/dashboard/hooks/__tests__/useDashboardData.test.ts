/**
 * Unit tests for `useDashboardData` BP-159 hot/cold split.
 *
 * Coverage:
 *   - mount performs the FULL refresh (all six IPCs, 500-row sample).
 *   - `transcription_final` with count+1 applies the DELTA (10-row head
 *     + count + corrections only; get_config/get_model_status/get_status
 *     NOT re-fetched; new row prepended + capped).
 *   - count jump (+2, import/restore path) falls back to the full refresh.
 *   - empty delta head falls back to the full refresh.
 *   - duplicate head id (already in sample) falls back to the full refresh.
 *   - `config_changed` triggers the FULL refresh (cold getters live there).
 *   - corrections card updates from the hot path alone.
 *
 * Strategy: pass a stub `call` directly (the hook takes it as an arg),
 * mock `usePythonEvent` to capture subscribers, stub
 * `document.visibilityState` to "visible" (jsdom defaults to
 * "prerender", which the hook treats as hidden).
 */
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
}));

vi.mock("@/lib/ipcCache", () => ({
	peekIpcCache: () => null,
	writeIpcCache: vi.fn(),
}));

import type { VoiceTyperConfig } from "@/types/config";
import type { HistoryRecord, ModelStatusMap } from "@/types/ipc";
import type { CorrectionUsageSnapshot } from "../../lib/streaks";
import {
	DASHBOARD_DELTA_LIMIT,
	DASHBOARD_SAMPLE_LIMIT,
	useDashboardData,
} from "../useDashboardData";

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

function makeConfig(): VoiceTyperConfig {
	return {
		model_size: "tiny",
		device: "cpu",
		language: "en",
	} as VoiceTyperConfig;
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
			if (cmd === "get_status") return Promise.resolve({ config_dir: "/cfg" });
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

	it("mount performs the full refresh (six IPCs, 500-row sample)", async () => {
		const rows = [makeRow(2), makeRow(1)];
		const { result } = await mountWith(rows, 2);

		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_history_count").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_status").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_correction_usage").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_model_status").length).toBeGreaterThan(0);
		const historyCalls = callsOf(callMock, "get_history");
		expect(historyCalls.length).toBeGreaterThan(0);
		expect(historyCalls[0]?.[1]).toEqual({ limit: DASHBOARD_SAMPLE_LIMIT });
		expect(result.current.data?.totalCount).toBe(2);
		expect(result.current.data?.sampleSize).toBe(2);
		expect(result.current.configDir).toBe("/cfg");
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
		expect(callsOf(callMock, "get_status")).toHaveLength(0);
		// New row prepended, count bumped, cold-derived fields intact.
		expect(result.current.data?.totalCount).toBe(3);
		expect(result.current.data?.sampleSize).toBe(3);
		expect(result.current.data?.todayCount).toBe(3);
		expect(result.current.configDir).toBe("/cfg");
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
				if (cmd === "get_status")
					return Promise.resolve({ config_dir: "/cfg" });
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
			if (cmd === "get_status") return Promise.resolve({ config_dir: "/cfg" });
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
			if (cmd === "get_status") return Promise.resolve({ config_dir: "/cfg" });
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
			if (cmd === "get_status") return Promise.resolve({ config_dir: "/cfg" });
			if (cmd === "get_correction_usage") return Promise.resolve(null);
			if (cmd === "get_model_status")
				return Promise.resolve({} as ModelStatusMap);
			return Promise.resolve(null);
		});

		await fireEvent("config_changed");

		expect(callsOf(callMock, "get_config").length).toBeGreaterThan(0);
		expect(callsOf(callMock, "get_model_status").length).toBeGreaterThan(0);
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
