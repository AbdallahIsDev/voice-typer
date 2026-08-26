/**
 * Unit tests for `useMicrophoneTest` — the composition hook over
 * `useMicrophoneLevelMonitor` + `useMicrophoneTestSession` +
 * `useMicrophonePlayback`.
 *
 * Coverage :
 *   - test start/stop lifecycle: startTest invokes microphone_test_start IPC,
 *     stopTest invokes microphone_test_stop IPC, testRunning state flips
 *   - test start clears prior testAudioBase64 + rawAudioBase64 +
 *     testDurationMs + testQuality before starting
 *   - handlePresetChange / handleConfigChange: thin wrappers around updateConfig
 *   - fixed test duration: the start payload carries MICROPHONE_TEST_DURATION_SEC;
 *     no testDurationSec / setTestDurationSec remains in the public API
 *   - smoke: composition renders without crashing + exposes the full return shape
 *
 * Strategy: mock usePython (call) + usePythonEvent + useSnackbar + Audio +
 * the rAF loop (level monitor uses requestAnimationFrame internally).
 * The meterRef points at a real DOM tree (matches the existing
 * useMicrophoneLevelMonitor test pattern).
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks (hoisted) ──────────────────────────────────────────────────
const { callMock, usePythonEventMock, showSnackMock } = vi.hoisted(() => ({
	callMock: vi.fn(),
	usePythonEventMock: vi.fn(),
	showSnackMock: vi.fn(),
}));

const stable = vi.hoisted(() => ({
	clearSnack: vi.fn(),
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
	useSnackbar: () => ({
		showSnack: showSnackMock,
		clearSnack: stable.clearSnack,
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

// ── Audio + rAF stubs (jsdom-friendly) ───────────────────────────────
// The composition hook indirectly uses `new Audio(...)` via the playback
// sub-hook + requestAnimationFrame via the level monitor. Stub both so the
// composition can mount cleanly without real audio hardware or a layout engine.
interface AudioStub {
	src: string;
	onended: (() => void) | null;
	onerror: (() => void) | null;
	play: ReturnType<typeof vi.fn>;
	pause: ReturnType<typeof vi.fn>;
}

const audioInstances: AudioStub[] = [];

beforeEach(() => {
	vi.clearAllMocks();
	audioInstances.length = 0;
	vi.stubGlobal(
		"Audio",
		vi.fn(function Audio(this: unknown, src: string) {
			const stub: AudioStub = {
				src,
				onended: null,
				onerror: null,
				play: vi.fn(() => Promise.resolve()),
				pause: vi.fn(),
			};
			audioInstances.push(stub);
			return stub;
		}),
	);
	// rAF stub: jsdom's rAF fires via setInterval. Replace with a no-op
	// so the level monitor's rAF loop doesn't spin during the test (we
	// don't need to assert on level values here — that's covered by the
	// dedicated useMicrophoneLevelMonitor tests).
	vi.stubGlobal(
		"requestAnimationFrame",
		vi.fn(() => 1),
	);
	vi.stubGlobal("cancelAnimationFrame", vi.fn());
	callMock.mockReset();
	usePythonEventMock.mockReset();
	showSnackMock.mockReset();
	// Default: every IPC returns a success envelope. Individual tests
	// override specific commands with mockImplementationOnce / mockImplementation.
	// Without this, the level monitor's `call("level_monitor_start")`
	// returns undefined and the `.catch(...)` on the resulting Promise
	// throws (the mock returns `undefined`, not a Promise).
	callMock.mockImplementation(() => Promise.resolve({ success: true }));
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.clearAllMocks();
});

import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
import { useMicrophoneTest } from "../useMicrophoneTest";
// ── Helpers ──────────────────────────────────────────────────────────
import { MICROPHONE_TEST_DURATION_SEC } from "../useMicrophoneTestSession";

function makeConfig(
	overrides: Partial<VoiceTyperConfig> = {},
): VoiceTyperConfig {
	return {
		schema_version: 1,
		hotkey: "<f2>",
		sample_rate: 16000,
		microphone: "mic-1",
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
		// Biometric consent granted so the level-monitor mount
		// effect (gated on ``voice_biometric_consent``) still fires
		// ``level_monitor_start`` in these tests.
		voice_biometric_consent: true,
		...overrides,
	} as VoiceTyperConfig;
}

function makeMicrophones(): MicrophoneDevice[] {
	return [{ id: "mic-1", name: "USB Mic", index: 0 }] as MicrophoneDevice[];
}

function makeMeterRef() {
	const div = document.createElement("div");
	const progress = document.createElement("div");
	progress.setAttribute("role", "progressbar");
	const fill = document.createElement("div");
	progress.appendChild(fill);
	div.appendChild(progress);
	return { current: div } as React.RefObject<HTMLElement | null>;
}

function makeSelectMicrophoneRef() {
	return {
		current: vi.fn().mockResolvedValue(undefined),
	} as unknown as React.MutableRefObject<
		(micId: string | null) => Promise<void>
	>;
}

function makeHookArgs(configOverrides: Partial<VoiceTyperConfig> = {}) {
	const config = makeConfig(configOverrides);
	const microphones = makeMicrophones();
	const setConfig = vi.fn();
	const updateConfig = vi.fn();
	const selectMicrophoneRef = makeSelectMicrophoneRef();
	const meterRef = makeMeterRef();
	return {
		config,
		microphones,
		setConfig,
		updateConfig,
		selectMicrophoneRef,
		meterRef,
	};
}

describe("useMicrophoneTest — composition smoke test (renders without crashing)", () => {
	it("exposes the full return shape (level/peak/testRunning/...)", () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTest(args));

		// Sanity: every documented public field is present.
		const r = result.current;
		expect(typeof r.startTest).toBe("function");
		expect(typeof r.stopTest).toBe("function");
		expect(typeof r.selectMicrophone).toBe("function");
		expect(typeof r.playAudio).toBe("function");
		expect(typeof r.stopPlayback).toBe("function");
		expect(typeof r.handlePresetChange).toBe("function");
		expect(typeof r.handleConfigChange).toBe("function");
		expect(typeof r.testRunning).toBe("boolean");
		expect(typeof r.testCountdown).toBe("number");
		expect(typeof r.testElapsed).toBe("number");
		expect(typeof r.testDurationMs).toBe("number");
		expect(typeof r.showAdvanced).toBe("boolean");
		expect(typeof r.filtersSinceLastTest).toBe("string");
		expect(typeof r.playingEnhanced).toBe("boolean");
		expect(typeof r.playingOriginal).toBe("boolean");
		expect(typeof r.level).toBe("number");
		expect(typeof r.peak).toBe("number");
		expect(typeof r.micMonitoring).toBe("boolean");
		// Live refs exposed.
		expect(r.levelRef).toHaveProperty("current");
		expect(r.peakRef).toHaveProperty("current");
	});

	it("exposes the fixed test duration + no dead duration configurability", () => {
		// The test duration is permanently MICROPHONE_TEST_DURATION_SEC —
		// the former user-configurable state/setter pair is gone from the
		// public API.
		expect(MICROPHONE_TEST_DURATION_SEC).toBe(10);

		const { result } = renderHook(() => useMicrophoneTest(makeHookArgs()));
		expect(result.current.showAdvanced).toBe(false);
		expect("testDurationSec" in result.current).toBe(false);
		expect("setTestDurationSec" in result.current).toBe(false);
	});
});

describe("useMicrophoneTest — test start/stop lifecycle", () => {
	it("startTest invokes microphone_test_start IPC + flips testRunning=true", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 5,
					sample_rate: 16000,
				});
			if (cmd === "level_monitor_start") return Promise.resolve({});
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useMicrophoneTest(makeHookArgs()));

		await act(async () => {
			await result.current.startTest();
		});

		// microphone_test_start IPC fired with the active mic id + the
		// fixed duration constant.
		const startCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_start",
		);
		expect(startCalls.length).toBe(1);
		expect(startCalls[0]?.[1]).toMatchObject({
			mic_id: "mic-1",
			duration: MICROPHONE_TEST_DURATION_SEC,
		});

		// testRunning flipped to true (countdown timer armed).
		expect(result.current.testRunning).toBe(true);
		expect(result.current.testCountdown).toBeGreaterThan(0);
	});

	it("startTest clears stale testAudioBase64 / rawAudioBase64 / testQuality before starting", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 5,
					sample_rate: 16000,
				});
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useMicrophoneTest(makeHookArgs()));

		// Start the first test.
		await act(async () => {
			await result.current.startTest();
		});

		// Stop the first test — populates testAudioBase64 etc.
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_read_audio") {
				return Promise.resolve({
					success: true,
					data_b64: "clip-1",
					bytes_read: 6,
					total_bytes: 6,
					eof: true,
					message: "ok",
				});
			}
			if (cmd === "microphone_test_stop")
				return Promise.resolve({
					success: true,
					audio_file: { path: "mem://filtered/clip.wav", bytes: 6 },
					raw_audio_file: { path: "mem://raw/raw.wav", bytes: 5 },
					duration_ms: 5000,
					quality: "good",
				});
			return Promise.resolve({});
		});

		await act(async () => {
			await result.current.stopTest();
		});
		expect(result.current.testAudioBase64).toBe("clip-1");

		// Start a SECOND test — should clear the first test's audio
		// before the new test runs.
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 5,
					sample_rate: 16000,
				});
			return Promise.resolve({});
		});

		await act(async () => {
			await result.current.startTest();
		});

		expect(result.current.testAudioBase64).toBeNull();
		expect(result.current.rawAudioBase64).toBeNull();
		expect(result.current.testQuality).toBeNull();
		expect(result.current.testDurationMs).toBe(0);
	});

	it("stopTest invokes microphone_test_stop IPC + surfaces recorded snack on success", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 5,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_read_audio") {
				return Promise.resolve({
					success: true,
					data_b64: "clip-1",
					bytes_read: 6,
					total_bytes: 6,
					eof: true,
					message: "ok",
				});
			}
			if (cmd === "microphone_test_stop")
				return Promise.resolve({
					success: true,
					audio_file: { path: "mem://filtered/clip.wav", bytes: 6 },
					raw_audio_file: { path: "mem://raw/raw.wav", bytes: 5 },
					duration_ms: 5000,
					quality: "good",
				});
			return Promise.resolve({});
		});

		const { result } = renderHook(() => useMicrophoneTest(makeHookArgs()));

		// Start + stop.
		await act(async () => {
			await result.current.startTest();
		});
		await act(async () => {
			await result.current.stopTest();
		});

		const stopCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_stop",
		);
		expect(stopCalls.length).toBe(1);

		// Recorded snack surfaces with the duration.
		expect(showSnackMock).toHaveBeenCalledWith(
			expect.stringContaining("microphone.recorded"),
			"success",
		);

		// testRunning cleared + audio clip populated.
		expect(result.current.testRunning).toBe(false);
		expect(result.current.testAudioBase64).toBe("clip-1");
		expect(result.current.testDurationMs).toBe(5000);
		expect(result.current.testQuality).toBe("good");
	});
});

describe("useMicrophoneTest — biometric consent gating (GDPR Art. 9)", () => {
	it("does NOT call level_monitor_start when voice_biometric_consent is false", async () => {
		// Privacy gate: the level monitor opens a continuous
		// biometric-capture InputStream — the mount effect must skip
		// ``level_monitor_start`` + the one-shot poll until consent is
		// granted (previously the page spammed futile IPC calls on
		// every mount for non-consenting users).
		const args = makeHookArgs({ voice_biometric_consent: false });
		renderHook(() => useMicrophoneTest(args));

		// Flush the mount effect + one-shot poll microtasks.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		const startCalls = callMock.mock.calls.filter(
			(c) => c[0] === "level_monitor_start",
		);
		expect(startCalls.length).toBe(0);
	});

	it("calls level_monitor_start on mount when voice_biometric_consent is true", async () => {
		const args = makeHookArgs({ voice_biometric_consent: true });
		renderHook(() => useMicrophoneTest(args));

		// Flush the mount effect so the async ``level_monitor_start``
		// fires.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		const startCalls = callMock.mock.calls.filter(
			(c) => c[0] === "level_monitor_start",
		);
		expect(startCalls.length).toBeGreaterThanOrEqual(1);
	});

	it("starts the level monitor when consent is granted WHILE the page is mounted (false → true rerender)", async () => {
		// The mount effect lists ``config?.voice_biometric_consent`` in
		// its deps, so flipping consent from off → on via a rerender
		// must re-run the effect and fire ``level_monitor_start``.
		// Without this, a user who grants consent in Settings while
		// the Microphone page is already mounted would never get a
		// live meter until they navigated away and back.
		const firstArgs = makeHookArgs({ voice_biometric_consent: false });
		const { rerender } = renderHook(({ args }) => useMicrophoneTest(args), {
			initialProps: { args: firstArgs },
		});

		// Mount with consent off — no start.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "level_monitor_start").length,
		).toBe(0);

		// Grant consent → the effect re-runs and starts the monitor.
		const grantedArgs = makeHookArgs({ voice_biometric_consent: true });
		rerender({ args: grantedArgs });
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		const startCalls = callMock.mock.calls.filter(
			(c) => c[0] === "level_monitor_start",
		);
		expect(startCalls.length).toBeGreaterThanOrEqual(1);
	});

	it("stops the level monitor when consent is revoked WHILE the page is mounted (true → false rerender)", async () => {
		const firstArgs = makeHookArgs({ voice_biometric_consent: true });
		const { rerender } = renderHook(({ args }) => useMicrophoneTest(args), {
			initialProps: { args: firstArgs },
		});

		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});
		expect(
			callMock.mock.calls.filter((c) => c[0] === "level_monitor_start").length,
		).toBeGreaterThanOrEqual(1);

		// Revoke consent → the effect's cleanup (``level_monitor_stop``)
		// runs and the early-return skips restarting.
		const revokedArgs = makeHookArgs({ voice_biometric_consent: false });
		rerender({ args: revokedArgs });
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		const stopCalls = callMock.mock.calls.filter(
			(c) => c[0] === "level_monitor_stop",
		);
		expect(stopCalls.length).toBeGreaterThanOrEqual(1);
	});
});

describe("useMicrophoneTest — handlePresetChange / handleConfigChange", () => {
	it("handlePresetChange delegates to updateConfig with the audio_preset field", () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTest(args));

		act(() => {
			result.current.handlePresetChange("studio" as never);
		});

		expect(args.updateConfig).toHaveBeenCalledWith({
			audio_preset: "studio",
		});
	});

	it("handleConfigChange delegates to updateConfig with the provided updates", () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTest(args));

		act(() => {
			result.current.handleConfigChange({ vad_threshold: 0.5 } as never);
		});

		expect(args.updateConfig).toHaveBeenCalledWith({ vad_threshold: 0.5 });
	});
});

describe("useMicrophoneTest — setter pass-through", () => {
	it("setShowAdvanced toggles the showAdvanced state", () => {
		const { result } = renderHook(() => useMicrophoneTest(makeHookArgs()));
		expect(result.current.showAdvanced).toBe(false);

		act(() => {
			result.current.setShowAdvanced(true);
		});
		expect(result.current.showAdvanced).toBe(true);
	});
});

describe("useMicrophoneTest — unmount cleanup cancels in-flight test", () => {
	it("does NOT throw on unmount while a test is running (cleanup cancels the test)", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 30,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_cancel") return Promise.resolve({});
			return Promise.resolve({});
		});

		const { result, unmount } = renderHook(() =>
			useMicrophoneTest(makeHookArgs()),
		);

		await act(async () => {
			await result.current.startTest();
		});
		expect(result.current.testRunning).toBe(true);

		// Unmount while the test is running — the session hook's cleanup
		// should send microphone_test_cancel + clear the timers.
		expect(() => unmount()).not.toThrow();

		// Wait for any pending async cleanup (microphone_test_cancel).
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		// microphone_test_cancel was sent by the cleanup.
		const cancelCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_cancel",
		);
		expect(cancelCalls.length).toBe(1);
	});
});
