/**
 * Unit tests for `useMicrophoneTestSession`.
 *
 * Coverage :
 *   - session lifecycle: startTest → countdown timer armed + testRunning=true,
 *     stopTest → microphone_test_stop IPC + recorded snack
 *   - device-swap handling: selectMicrophone cancels an in-flight test,
 *     sends set_config with the new micId, surfaces "usingMic" snack
 *   - microphone_test_complete event drives stopTest when the backend
 *     finishes recording
 *   - selectMicrophoneRef is assigned the latest stable closure so the
 *     sibling useMicrophoneData hook can invoke it on hot-swap
 *   - unmount cleanup cancels the in-flight test + clears timers
 *
 * Strategy: renderHook with mocked `call` + captured `usePythonEvent`
 * subscriber. The hook receives its deps as plain args (no React context
 * needed) — the composition hook normally passes them in.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ── Mocks (hoisted) ──────────────────────────────────────────────────
const { callMock, usePythonEventMock } = vi.hoisted(() => ({
	callMock: vi.fn(),
	usePythonEventMock: vi.fn(),
}));

vi.mock("@/hooks/usePython", () => ({
	usePythonEvent: usePythonEventMock,
	// The session hook receives `call` as an arg (from the composition
	// hook's usePython() call), so we don't need to mock usePython here.
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

import { useConsentGateStore } from "@/lib/consentGate";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
// ── Helpers ──────────────────────────────────────────────────────────
import { useMicrophoneTestSession } from "../useMicrophoneTestSession";

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
		voice_biometric_consent: true,
		...overrides,
	} as VoiceTyperConfig;
}

function makeMicrophones(): MicrophoneDevice[] {
	return [
		{ id: "mic-1", name: "USB Mic", index: 0 },
		{ id: "mic-2", name: "Built-in", index: 1 },
	] as MicrophoneDevice[];
}

function makeHookArgs() {
	const setConfig = vi.fn();
	const updateConfig = vi.fn();
	const setLevel = vi.fn();
	const setPeak = vi.fn();
	const setMicMonitoring = vi.fn();
	const stopPlayback = vi.fn();
	const testRunningRef = { current: false };
	const selectMicrophoneRef = {
		current: vi.fn().mockResolvedValue(undefined),
	} as unknown as React.MutableRefObject<
		(micId: string | null) => Promise<void>
	>;

	return {
		call: callMock as unknown as <T = unknown>(
			cmd: string,
			data?: Record<string, unknown>,
		) => Promise<T>,
		config: makeConfig(),
		microphones: makeMicrophones(),
		setConfig,
		updateConfig,
		showSnack: vi.fn(),
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
		testDurationSec: 10,
		setLevel,
		setPeak,
		setMicMonitoring,
		stopPlayback,
		testRunningRef,
		selectMicrophoneRef,
	};
}

function getMicrophoneTestCompleteHandler() {
	// usePythonEvent is called on every render (it's a hook). Each call
	// registers a new closure that captures the LATEST `testRunning` and
	// `stopTest` values. We want the LAST registered handler so we invoke
	// the closure that sees the current state.
	const calls = usePythonEventMock.mock.calls.filter(
		(c) => c[0] === "microphone_test_complete",
	);
	const last = calls[calls.length - 1];
	return last?.[1] as
		| ((data?: unknown) => (() => void) | undefined)
		| undefined;
}

beforeEach(() => {
	callMock.mockReset();
	usePythonEventMock.mockReset();
	// Default: every IPC returns a success envelope.
	callMock.mockImplementation(() => Promise.resolve({ success: true }));
	useConsentGateStore.setState({ request: null });
});

afterEach(() => {
	vi.clearAllMocks();
	useConsentGateStore.setState({ request: null });
});

describe("useMicrophoneTestSession — initial state", () => {
	it("exposes testRunning=false, testCountdown=0, testAudioBase64=null, testQuality=null on mount", () => {
		const { result } = renderHook(() =>
			useMicrophoneTestSession(makeHookArgs()),
		);
		expect(result.current.testRunning).toBe(false);
		expect(result.current.testCountdown).toBe(0);
		expect(result.current.testElapsed).toBe(0);
		expect(result.current.testAudioBase64).toBeNull();
		expect(result.current.rawAudioBase64).toBeNull();
		expect(result.current.testDurationMs).toBe(0);
		expect(result.current.testQuality).toBeNull();
		expect(result.current.filtersSinceLastTest).toBe("");
	});
});

describe("useMicrophoneTestSession — startTest lifecycle", () => {
	it("invokes microphone_test_start IPC with mic_id + duration + filters", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 10,
					sample_rate: 16000,
				});
			return Promise.resolve({ success: true });
		});

		const { result } = renderHook(() =>
			useMicrophoneTestSession(makeHookArgs()),
		);

		await act(async () => {
			await result.current.startTest();
		});

		const startCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_start",
		);
		expect(startCalls.length).toBe(1);
		expect(startCalls[0]?.[1]).toMatchObject({
			mic_id: "mic-1",
			duration: 10,
		});

		// testRunning flipped to true + countdown armed.
		expect(result.current.testRunning).toBe(true);
		expect(result.current.testCountdown).toBeGreaterThan(0);
	});

	it("surfaces startTestFailed snack when the backend reports success=false", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: false,
					message: "device busy",
					duration: 0,
					sample_rate: 0,
				});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.startTest();
		});

		// testRunning stays false — start failed.
		expect(result.current.testRunning).toBe(false);
		// Error snack surfaced with the backend's message.
		expect(args.showSnack).toHaveBeenCalledWith("device busy", "error");
	});

	it("clears prior testAudioBase64 / testQuality / testDurationMs before starting a new test", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 5,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_stop")
				return Promise.resolve({
					success: true,
					audio_base64: "clip-1",
					raw_audio_base64: "raw-1",
					duration_ms: 5000,
					quality: "good",
				});
			return Promise.resolve({ success: true });
		});

		const { result } = renderHook(() =>
			useMicrophoneTestSession(makeHookArgs()),
		);

		// Start + stop the first test to populate audio state.
		await act(async () => {
			await result.current.startTest();
		});
		await act(async () => {
			await result.current.stopTest();
		});
		expect(result.current.testAudioBase64).toBe("clip-1");

		// Start a SECOND test — should clear the first test's audio.
		await act(async () => {
			await result.current.startTest();
		});

		expect(result.current.testAudioBase64).toBeNull();
		expect(result.current.rawAudioBase64).toBeNull();
		expect(result.current.testDurationMs).toBe(0);
		expect(result.current.testQuality).toBeNull();
	});
});
describe("useMicrophoneTestSession — biometric consent required (GDPR Art. 9)", () => {
	it("opens the consent gate with a retry when the backend resolves success=false with 'consent required'", async () => {
		// The backend's ``client.consent_required`` envelope (from
		// ``_respond_with_error``'s ConsentRequiredError mapping) resolves
		// as a ``success:false`` result whose message contains
		// "consent required" — the renderer must open the unified
		// point-of-use consent dialog (Allow → persist → retry the full
		// test start) instead of a generic failure toast.
		let startCalls = 0;
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start") {
				startCalls += 1;
				if (startCalls === 1)
					return Promise.resolve({
						success: false,
						message:
							"voice biometric consent required to start microphone test",
						duration: 0,
						sample_rate: 0,
						// Structured field the level-monitor / mic-test
						// handlers attach — names the EXACT Settings toggle.
						code: "client.consent_required",
						consent_field: "voice_biometric_consent",
					});
				// Retry (Allow in the dialog): the test now starts.
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 10,
					sample_rate: 16000,
				});
			}
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.startTest();
		});

		// testRunning stays false — the test did not start.
		expect(result.current.testRunning).toBe(false);
		// The unified consent gate opened with the exact field + a
		// retry closure.
		const req = useConsentGateStore.getState().request;
		expect(req).toEqual(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				bodyKey: "consentDialog.field.voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);
		// The retry re-runs the FULL start — the test actually starts.
		await act(async () => {
			await req?.onAllow?.();
		});
		expect(result.current.testRunning).toBe(true);
	});

	it("defaults the consent field to voice_biometric_consent when the resolved envelope omits it", async () => {
		// Older backends / WS-path envelopes may resolve a plain
		// ``success:false`` + message without the structured
		// ``consent_field`` — the gate must still name the
		// voice-biometric toggle (the only field these gates enforce).
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: false,
					message: "voice biometric consent required to start microphone test",
					duration: 0,
					sample_rate: 0,
				});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.startTest();
		});

		expect(result.current.testRunning).toBe(false);
		expect(useConsentGateStore.getState().request).toEqual(
			expect.objectContaining({ consentField: "voice_biometric_consent" }),
		);
	});

	it("opens the consent gate when the IPC throws an Error with code client.consent_required", async () => {
		// Electron path: the ``type:"error"`` envelope is thrown as an
		// Error with ``code`` preserved by ``usePython.call``. The hook
		// must detect ``code === "client.consent_required"`` and open
		// the consent gate instead of the generic failure toast.
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start") {
				const err = new Error(
					"voice biometric consent required to start microphone test",
				);
				(err as { code?: string }).code = "client.consent_required";
				(err as { consent_field?: string }).consent_field =
					"voice_biometric_consent";
				return Promise.reject(err);
			}
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.startTest();
		});

		expect(result.current.testRunning).toBe(false);
		expect(useConsentGateStore.getState().request).toEqual(
			expect.objectContaining({
				consentField: "voice_biometric_consent",
				onAllow: expect.any(Function),
			}),
		);
	});

	it("forwards the consent_field from a thrown client.consent_required Error to the consent gate", async () => {
		// Same as above — the structured ``consent_field`` (preserved
		// by usePython.call) must reach the gate so the dialog + the
		// Settings deep-link target the exact toggle.
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start") {
				const err = new Error(
					"voice biometric consent required to start microphone test",
				);
				(err as { code?: string }).code = "client.consent_required";
				(err as { consent_field?: string }).consent_field =
					"voice_biometric_consent";
				return Promise.reject(err);
			}
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.startTest();
		});

		expect(useConsentGateStore.getState().request).toEqual(
			expect.objectContaining({ consentField: "voice_biometric_consent" }),
		);
	});

	it("falls back to the generic failure toast for non-consent failures", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: false,
					message: "device busy",
					duration: 0,
					sample_rate: 0,
				});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.startTest();
		});

		expect(result.current.testRunning).toBe(false);
		expect(args.showSnack).toHaveBeenCalledWith("device busy", "error");
	});
});

describe("useMicrophoneTestSession — stopTest lifecycle", () => {
	it("invokes microphone_test_stop IPC + surfaces recorded snack on success", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 5,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_stop")
				return Promise.resolve({
					success: true,
					audio_base64: "clip-1",
					raw_audio_base64: "raw-1",
					duration_ms: 5000,
					quality: "good",
				});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

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

		// Recorded snack surfaces with the duration (5.0s).
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("microphone.recorded"),
			"success",
		);

		// Audio clip + quality populated.
		expect(result.current.testAudioBase64).toBe("clip-1");
		expect(result.current.testDurationMs).toBe(5000);
		expect(result.current.testQuality).toBe("good");
		// testRunning cleared.
		expect(result.current.testRunning).toBe(false);
	});

	it("surfaces a warning snack when the backend reports success=true but no audio", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_stop")
				return Promise.resolve({ success: true }); // no audio_base64
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.stopTest();
		});

		// "noAudio" warning surfaced (with the "try default mic" suffix
		// because the config has a non-null microphone).
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("microphone.noAudio"),
			"warning",
		);
	});

	it("surfaces an error snack when the backend reports success=false", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_stop")
				return Promise.resolve({
					success: false,
					message: "recorder crashed",
				});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.stopTest();
		});

		expect(args.showSnack).toHaveBeenCalledWith("recorder crashed", "error");
	});
});

describe("useMicrophoneTestSession — device-swap handling (selectMicrophone)", () => {
	it("cancels the in-flight test + sends set_config with the new micId", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 30,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_cancel") return Promise.resolve({});
			if (cmd === "set_config") return Promise.resolve({});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		// Start a test.
		await act(async () => {
			await result.current.startTest();
		});
		expect(result.current.testRunning).toBe(true);

		// Swap to mic-2 mid-test.
		await act(async () => {
			await result.current.selectMicrophone("mic-2");
		});

		// microphone_test_cancel was sent at least once (the
		// selectMicrophone call cancels the in-flight test; the
		// useEffect cleanup may fire an additional cancel when
		// testRunning transitions true → false because the cleanup
		// closure captured the prior `testRunning=true` state).
		const cancelCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_cancel",
		);
		expect(cancelCalls.length).toBeGreaterThanOrEqual(1);

		// set_config persisted the new micId.
		const setConfigCalls = callMock.mock.calls.filter(
			(c) => c[0] === "set_config",
		);
		expect(setConfigCalls.length).toBe(1);
		expect(setConfigCalls[0]?.[1]).toEqual({ microphone: "mic-2" });

		// Local config snapshot updated optimistically.
		expect(args.setConfig).toHaveBeenCalled();

		// testRunning cleared + audio state cleared.
		expect(result.current.testRunning).toBe(false);
		expect(result.current.testAudioBase64).toBeNull();

		// "usingMic" success snack surfaced.
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("microphone.usingMic"),
			"success",
		);
	});

	it("selectMicrophone(null) falls back to system default + surfaces the usingMic snack with the systemDefault label", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.selectMicrophone(null);
		});

		expect(callMock).toHaveBeenCalledWith("set_config", { microphone: null });
		expect(args.showSnack).toHaveBeenCalledWith(
			expect.stringContaining("microphone.usingMic"),
			"success",
		);
	});

	it("surfaces setFailed snack when set_config throws", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "set_config")
				return Promise.reject(new Error("config write failed"));
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		await act(async () => {
			await result.current.selectMicrophone("mic-2");
		});

		expect(args.showSnack).toHaveBeenCalledWith(
			"microphone.setFailed",
			"error",
		);
	});
});

describe("useMicrophoneTestSession — microphone_test_complete event", () => {
	it("drives stopTest when the event fires while a test is running", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 30,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_stop")
				return Promise.resolve({
					success: true,
					audio_base64: "clip-1",
					raw_audio_base64: null,
					duration_ms: 5000,
					quality: "good",
				});
			return Promise.resolve({ success: true });
		});

		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		// Start a test.
		await act(async () => {
			await result.current.startTest();
		});
		expect(result.current.testRunning).toBe(true);

		// Backend signals completion via the push event.
		const handler = getMicrophoneTestCompleteHandler();
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.({});
			// Give the async stopTest time to complete.
			await new Promise((r) => setTimeout(r, 0));
		});

		// microphone_test_stop was sent.
		const stopCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_stop",
		);
		expect(stopCalls.length).toBe(1);

		// Audio state populated.
		expect(result.current.testAudioBase64).toBe("clip-1");
		expect(result.current.testRunning).toBe(false);
	});

	it("does NOT invoke stopTest when the event fires while no test is running (defensive guard)", async () => {
		const args = makeHookArgs();
		renderHook(() => useMicrophoneTestSession(args));

		// No test is running. Fire the event — should be a no-op.
		const handler = getMicrophoneTestCompleteHandler();
		expect(handler).toBeDefined();

		await act(async () => {
			handler?.({});
			await new Promise((r) => setTimeout(r, 0));
		});

		// microphone_test_stop NEVER sent.
		const stopCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_stop",
		);
		expect(stopCalls.length).toBe(0);
	});
});

describe("useMicrophoneTestSession — selectMicrophoneRef assignment", () => {
	it("assigns the latest selectMicrophone closure to selectMicrophoneRef.current", async () => {
		const args = makeHookArgs();
		const { result } = renderHook(() => useMicrophoneTestSession(args));

		// Wait for the assignment effect to run.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		// selectMicrophoneRef.current should now be a function (the
		// hook's stable selectMicrophone closure). It was initialized
		// as a vi.fn() — verify it's been REPLACED with the hook's closure.
		expect(typeof args.selectMicrophoneRef.current).toBe("function");
		// The new closure is the hook's selectMicrophone (not the
		// initial vi.fn() mock). We verify by invoking it and checking
		// that it dispatched set_config.
		await act(async () => {
			await args.selectMicrophoneRef.current("mic-2");
		});

		const setConfigCalls = callMock.mock.calls.filter(
			(c) => c[0] === "set_config",
		);
		expect(setConfigCalls.length).toBe(1);
		expect(setConfigCalls[0]?.[1]).toEqual({ microphone: "mic-2" });
		// Sanity: result.current.selectMicrophone is the same function.
		expect(args.selectMicrophoneRef.current).toBe(
			result.current.selectMicrophone,
		);
	});
});

describe("useMicrophoneTestSession — unmount cleanup", () => {
	it("cancels the in-flight test on unmount + clears timers", async () => {
		callMock.mockImplementation((cmd: string) => {
			if (cmd === "microphone_test_start")
				return Promise.resolve({
					success: true,
					message: "ok",
					duration: 30,
					sample_rate: 16000,
				});
			if (cmd === "microphone_test_cancel") return Promise.resolve({});
			return Promise.resolve({ success: true });
		});

		const { result, unmount } = renderHook(() =>
			useMicrophoneTestSession(makeHookArgs()),
		);

		await act(async () => {
			await result.current.startTest();
		});
		expect(result.current.testRunning).toBe(true);

		// Unmount — the cleanup should send microphone_test_cancel.
		unmount();

		// Flush the microtask queue so the cancel Promise resolves.
		await act(async () => {
			await new Promise((r) => setTimeout(r, 0));
		});

		const cancelCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_cancel",
		);
		expect(cancelCalls.length).toBe(1);
	});

	it("does NOT send microphone_test_cancel on unmount when no test is running", () => {
		const { unmount } = renderHook(() =>
			useMicrophoneTestSession(makeHookArgs()),
		);

		// No test started — unmount should NOT send microphone_test_cancel.
		unmount();

		const cancelCalls = callMock.mock.calls.filter(
			(c) => c[0] === "microphone_test_cancel",
		);
		expect(cancelCalls.length).toBe(0);
	});
});
