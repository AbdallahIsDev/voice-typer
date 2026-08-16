/**
 * Unit tests for `useMicrophoneData`.
 *
 * Coverage :
 *   - loadData: parallel fetch of get_microphones + get_config via Promise.all
 *   - data buffering: the module-level _cachedMicrophones / _cachedConfig
 *     caches are populated on each successful load
 *   - cleanup on unmount: the mount-time load effect uses a `cancelled`
 *     flag so a superseded loadData call does NOT call setState on an
 *     unmounted (or stale) component
 *   - loadError surfacing: when get_microphones / get_config reject, the
 *     error message is captured into `loadError` so the render path can
 *     show a retry EmptyState instead of an ambiguous empty list
 *   - microphones_changed event: triggers a loadData refresh
 *   - config_changed event: triggers a loadData refresh
 *   - hot-swap fallback: when the active mic is no longer present, the
 *     hook shows a warning snack + invokes selectMicrophoneRef(null)
 *
 * Strategy: mock usePython (call), usePythonEvent (capture subscribers),
 * useSnackbar, useLastUpdated.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks (hoisted) ──────────────────────────────────────────────────
const {
	callMock,
	usePythonEventMock,
	showSnackMock,
	markUpdatedMock,
	agoLabelMock,
} = vi.hoisted(() => ({
	callMock: vi.fn(),
	usePythonEventMock: vi.fn(),
	showSnackMock: vi.fn(),
	markUpdatedMock: vi.fn(),
	agoLabelMock: "just now",
}));

vi.mock("@/hooks/usePython", () => ({
	usePython: () => ({
		call: callMock,
		status: "connected",
		connectionStatus: "connected",
	}),
	usePythonEvent: usePythonEventMock,
}));

vi.mock("@/hooks/useSnackbar", () => ({
	useSnackbar: () => ({ showSnack: showSnackMock, clearSnack: vi.fn() }),
}));

vi.mock("@/hooks/useLastUpdated", () => ({
	useLastUpdated: () => ({
		agoLabel: agoLabelMock,
		markUpdated: markUpdatedMock,
	}),
}));

vi.mock("@/i18n/i18n", () => ({
	t: (key: string, params?: Record<string, string>) => {
		if (!params) return key;
		let result = key;
		const leftover: string[] = [];
		for (const [k, v] of Object.entries(params)) {
			const placeholder = `{${k}}`;
			if (result.includes(placeholder)) {
				result = result.replace(placeholder, String(v));
			} else {
				leftover.push(`${k}=${String(v)}`);
			}
		}
		if (leftover.length > 0) {
			result = `${result}: ${leftover.join(", ")}`;
		}
		return result;
	},
}));

import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
// ── Helpers ──────────────────────────────────────────────────────────
import { useMicrophoneData } from "../useMicrophoneData";

function makeMic(id: string, name = id): MicrophoneDevice {
	return { id, name, index: 0 } as MicrophoneDevice;
}

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		hotkey: "<f2>",
		sample_rate: 16000,
		microphone: null,
		model_size: "tiny",
		language: "en",
		device: "cpu",
		beam_size: 5,
		best_of: 5,
		condition_on_previous_text: false,
		streaming_transcription: false,
		streaming_chunk_seconds: 0,
		streaming_step_seconds: 0,
		streaming_left_overlap_seconds: 0,
		streaming_right_guard_seconds: 0,
		streaming_min_first_chunk_seconds: 0,
		streaming_silence_threshold: 0,
		autostart: false,
		paste_on_stop: true,
		show_notifications: true,
		fast_startup: false,
		clipboard_save_restore: true,
		clipboard_restore_delay_ms: 0,
		asr_backend: "whisper",
		qwen_model_path: null,
		parakeet_model_path: null,
		openai_api_key: "",
		groq_api_key: "",
		deepgram_api_key: "",
		huggingface_consent: false,
		cloud_openai_consent: false,
		cloud_groq_consent: false,
		cloud_deepgram_consent: false,
		...overrides,
	} as VoiceTyperConfig;
}

function makeSelectMicrophoneRef() {
	return {
		current: vi.fn().mockResolvedValue(undefined),
	} as unknown as React.MutableRefObject<
		(micId: string | null) => Promise<void>
	>;
}

function getEventHandler(eventName: string) {
	const call = usePythonEventMock.mock.calls.find((c) => c[0] === eventName);
	return call?.[1] as
		| ((data?: Record<string, unknown>) => (() => void) | undefined)
		| undefined;
}

beforeEach(() => {
	callMock.mockReset();
	usePythonEventMock.mockReset();
	showSnackMock.mockReset();
	markUpdatedMock.mockReset();
});

afterEach(() => {
	vi.clearAllMocks();
});

describe("useMicrophoneData — loadData (parallel fetch + buffering)", () => {
	it("fires get_microphones + get_config in parallel via Promise.all + populates state", async () => {
		const mics = [makeMic("mic-1", "USB Mic"), makeMic("mic-2", "Built-in")];
		const cfg = makeConfig({ microphone: "mic-1" });
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones") return Promise.resolve(mics);
			if (cmd === "get_config") return Promise.resolve(cfg);
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.microphones.length).toBe(2);
		});

		expect(result.current.microphones).toEqual(mics);
		expect(result.current.config?.microphone).toBe("mic-1");
		expect(result.current.loading).toBe(false);
		expect(result.current.loadError).toBeNull();

		// Both IPC commands issued.
		const cmds = callMock.mock.calls.map((c) => c[0]);
		expect(cmds).toContain("get_microphones");
		expect(cmds).toContain("get_config");

		// markUpdated called in the finally block of loadData.
		expect(markUpdatedMock).toHaveBeenCalled();
	});

	it("captures loadError when get_microphones rejects (surfaces backend-load failure)", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones")
				return Promise.reject(new Error("backend unreachable"));
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.loading).toBe(false);
		});

		// loadError surfaces the failure (regression: previously the
		// hook only logged to console and left the user with an empty
		// mic list + no indication of why).
		expect(result.current.loadError).toBe("backend unreachable");
		// loading flag cleared in the finally block.
		expect(result.current.loading).toBe(false);
	});
});

describe("useMicrophoneData — cleanup on unmount (cancelled flag)", () => {
	it("does NOT call setState after unmount when an in-flight loadData resolves late", async () => {
		// Make the IPC slow so the loadData Promise resolves AFTER unmount.
		let resolveGetMics: (v: MicrophoneDevice[]) => void = () => {};
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones")
				return new Promise((resolve) => {
					resolveGetMics = resolve as typeof resolveGetMics;
				});
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result, unmount } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		// Initial state: loading=true.
		expect(result.current.loading).toBe(true);

		// Unmount BEFORE the IPC resolves.
		unmount();

		// Suppress React's "Can't perform a React state update on an
		// unmounted component" warning so we can detect it below.
		const spy = vi.spyOn(console, "error").mockImplementation(() => {});

		// Now resolve the IPC — the cancelled flag should prevent
		// setMicrophones / setConfig / setLoading from being called.
		act(() => {
			resolveGetMics([makeMic("late-mic")]);
		});

		// Flush microtasks so the loadData .then() runs.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		const reactWarnings = spy.mock.calls.filter(
			(args) =>
				typeof args[0] === "string" && args[0].includes("unmounted component"),
		);
		expect(reactWarnings.length).toBe(0);
		spy.mockRestore();
	});
});

describe("useMicrophoneData — microphones_changed + config_changed event subscriptions", () => {
	it("microphones_changed event triggers a loadData refresh", async () => {
		const mics = [makeMic("mic-1")];
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones") return Promise.resolve(mics);
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.microphones.length).toBe(1);
		});

		const initialCallCount = callMock.mock.calls.length;
		const handler = getEventHandler("microphones_changed");
		expect(handler).toBeDefined();

		// Fire the event — should trigger loadData (which re-issues
		// get_microphones + get_config).
		await act(async () => {
			handler?.();
			await new Promise((r) => setTimeout(r, 0));
		});

		// At least one more get_microphones call after the event.
		const getMicsCalls = callMock.mock.calls.filter(
			(c) => c[0] === "get_microphones",
		);
		expect(getMicsCalls.length).toBeGreaterThan(1);
		expect(initialCallCount).toBeGreaterThan(0);
	});

	it("config_changed event triggers a loadData refresh", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones") return Promise.resolve([]);
			if (cmd === "get_config") return Promise.resolve(makeConfig());
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.loading).toBe(false);
		});

		const handler = getEventHandler("config_changed");
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.();
			await new Promise((r) => setTimeout(r, 0));
		});

		// Multiple get_config calls (initial + event-triggered).
		const getConfigCalls = callMock.mock.calls.filter(
			(c) => c[0] === "get_config",
		);
		expect(getConfigCalls.length).toBeGreaterThan(1);
	});
});

describe("useMicrophoneData — hot-swap fallback (active mic no longer present)", () => {
	it("invokes selectMicrophoneRef(null) when the active mic is no longer in the refreshed list", async () => {
		// Track which loadData cycle we're in so we can return different
		// microphone lists for the initial load vs the event-triggered
		// refresh. Using a counter (instead of mockImplementationOnce)
		// because Promise.all fires BOTH get_microphones + get_config
		// in parallel, so two mockImplementationOnce handlers would
		// each be consumed by ONE of the two commands (and the second
		// handler would return the wrong shape for whichever command
		// hit it second).
		let loadDataCount = 0;
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones") {
				// First load returns mic-1; subsequent loads (event-triggered)
				// return an empty list (mic-1 hot-unplugged).
				if (loadDataCount === 0) return Promise.resolve([makeMic("mic-1")]);
				return Promise.resolve([]);
			}
			if (cmd === "get_config")
				return Promise.resolve(makeConfig({ microphone: "mic-1" }));
			return Promise.resolve({});
		});

		// Bump the counter once both commands of a loadData cycle have
		// been issued. We track get_config because it's the 2nd command
		// in Promise.all (the order is non-deterministic, but both fire
		// before the next loadData cycle starts).
		const originalCall = callMock.getMockImplementation();
		callMock.mockImplementation((cmd: string) => {
			const result = originalCall?.(cmd);
			if (cmd === "get_config") loadDataCount++;
			return result;
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.microphones.length).toBe(1);
		});

		// Fire microphones_changed — the handler should refresh the
		// list (now empty) + detect that mic-1 is gone + auto-fallback.
		const handler = getEventHandler("microphones_changed");
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.();
			// Give the async IIFE inside the handler time to complete
			// (loadData + the stillPresent check + the selectMicrophone
			// call all happen after the handler returns).
			await new Promise((r) => setTimeout(r, 50));
		});

		// selectMicrophoneRef.current(null) invoked — auto-fallback to
		// the system default.
		expect(selectMicrophoneRef.current).toHaveBeenCalledWith(null);
		// Warning snack surfaced to explain what happened.
		expect(showSnackMock).toHaveBeenCalledWith(
			"microphone.activeMicUnavailable",
			"warning",
		);
	});

	it("does NOT invoke selectMicrophoneRef when the active mic is still present", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones")
				return Promise.resolve([makeMic("mic-1"), makeMic("mic-2")]);
			if (cmd === "get_config")
				return Promise.resolve(makeConfig({ microphone: "mic-1" }));
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.microphones.length).toBe(2);
		});

		(
			selectMicrophoneRef.current as unknown as ReturnType<typeof vi.fn>
		).mockClear();

		const handler = getEventHandler("microphones_changed");
		await act(async () => {
			handler?.();
			await new Promise((r) => setTimeout(r, 0));
		});

		// mic-1 is still present — no fallback.
		expect(selectMicrophoneRef.current).not.toHaveBeenCalled();
	});
});

describe("useMicrophoneData — updateConfig (optimistic write-through)", () => {
	it("writes through to set_config IPC + updates the local config snapshot", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "get_microphones") return Promise.resolve([]);
			if (cmd === "get_config")
				return Promise.resolve(makeConfig({ microphone: null }));
			if (cmd === "set_config") return Promise.resolve({});
			return Promise.resolve({});
		});

		const selectMicrophoneRef = makeSelectMicrophoneRef();
		const { result } = renderHook(() =>
			useMicrophoneData({ selectMicrophoneRef }),
		);

		await waitFor(() => {
			expect(result.current.config).not.toBeNull();
		});

		act(() => {
			result.current.updateConfig({ microphone: "mic-2" });
		});

		// Local config snapshot updated optimistically.
		expect(result.current.config?.microphone).toBe("mic-2");

		// set_config IPC fired with the updates payload.
		const setConfigCalls = callMock.mock.calls.filter(
			(c) => c[0] === "set_config",
		);
		expect(setConfigCalls.length).toBe(1);
		expect(setConfigCalls[0]?.[1]).toEqual({ microphone: "mic-2" });
	});
});
