/**
 * Focused tests for useOnboardingWizard's PARALLEL content fetch.
 *
 * The wizard's init previously made 5 SEQUENTIAL IPC round-trips
 * (onboarding_start → get_config → onboarding_get_microphones →
 * onboarding_get_hotkey_presets → onboarding_get_model_options), so
 * first-run content waited for the SUM of all five latencies. The four
 * content fetches now run via Promise.all AFTER onboarding_start (the
 * Dashboard pattern), with the results applied in the ORIGINAL order
 * (config prefill BEFORE the mic reconciliation).
 *
 * These tests pin:
 *   - the content fetches do NOT fire until onboarding_start resolves,
 *   - once it does, all four fire concurrently (all in flight while
 *     none has resolved),
 *   - content state + loading land only after the whole batch resolves,
 *   - config prefill → mic reconciliation ordering,
 *   - get_config failure stays non-fatal (wizard continues without
 *     prefill),
 *   - a content-fetch failure surfaces as initError (error paths
 *     preserved),
 *   - a content-fetch failure does NOT discard the already-fetched
 *     config prefill (Promise.allSettled + per-result unwrapping — the
 *     pre-parallel sequential code applied prefill before a later
 *     fetch could fail; a plain Promise.all batch had regressed that).
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
	pythonMock,
	resetStableMocks,
	sonnerMock,
	stableMocks,
} from "@/__tests__/helpers/stableMocks";

const { mockCall } = stableMocks;

vi.mock("@/hooks/usePython", () => pythonMock());
vi.mock("sonner", () => sonnerMock());

import { makeConfig } from "@/__tests__/helpers/fixtures";
import { useOnboardingWizard } from "@/pages/onboarding/hooks/useOnboardingWizard";
import type { MicrophoneOption, StepInfo } from "@/pages/onboarding/lib/types";
import type { VoiceTyperConfig } from "@/types/config";

/** Manually-resolved promise handle so tests control IPC timing. */
interface Deferred<T> {
	promise: Promise<T>;
	resolve: (value: T) => void;
	reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

interface InitControls {
	start: Deferred<StepInfo>;
	config: Deferred<VoiceTyperConfig>;
	mics: Deferred<{ microphones: MicrophoneOption[] }>;
	presets: Deferred<{ presets: string[] }>;
	models: Deferred<{ models: unknown[] }>;
}

const MIC_LIST: MicrophoneOption[] = [
	{ id: "mic-1", name: "Default mic", default: true },
	{ id: "mic-2", name: "USB mic" },
];

function wireControls(): InitControls {
	const controls: InitControls = {
		start: deferred<StepInfo>(),
		config: deferred<VoiceTyperConfig>(),
		mics: deferred<{ microphones: MicrophoneOption[] }>(),
		presets: deferred<{ presets: string[] }>(),
		models: deferred<{ models: unknown[] }>(),
	};
	mockCall.mockImplementation((type: string) => {
		switch (type) {
			case "onboarding_start":
				return controls.start.promise;
			case "get_config":
				return controls.config.promise;
			case "onboarding_get_microphones":
				return controls.mics.promise;
			case "onboarding_get_hotkey_presets":
				return controls.presets.promise;
			case "onboarding_get_model_options":
				return controls.models.promise;
			default:
				return Promise.resolve({});
		}
	});
	return controls;
}

/** Flush pending microtasks so in-flight promise chains advance. */
async function flush(times = 3) {
	for (let i = 0; i < times; i++) {
		await act(async () => {
			await Promise.resolve();
		});
	}
}

function callCount(type: string): number {
	return mockCall.mock.calls.filter((args: unknown[]) => args[0] === type)
		.length;
}

beforeEach(() => {
	resetStableMocks();
});

describe("useOnboardingWizard — parallel content fetch after onboarding_start", () => {
	it("waits for onboarding_start, then fires all four content fetches concurrently", async () => {
		const controls = wireControls();
		const { result } = renderHook(() => useOnboardingWizard());

		// Mount: only onboarding_start has been called.
		await flush();
		expect(callCount("onboarding_start")).toBe(1);
		expect(callCount("get_config")).toBe(0);
		expect(callCount("onboarding_get_microphones")).toBe(0);
		expect(callCount("onboarding_get_hotkey_presets")).toBe(0);
		expect(callCount("onboarding_get_model_options")).toBe(0);

		// Start resolves → all four content commands are IN FLIGHT at once
		// (none of their promises has resolved yet).
		await act(async () => {
			controls.start.resolve({
				step: 1,
				total_steps: 6,
				step_name: "Microphone",
			});
		});
		await flush();
		expect(callCount("get_config")).toBe(1);
		expect(callCount("onboarding_get_microphones")).toBe(1);
		expect(callCount("onboarding_get_hotkey_presets")).toBe(1);
		expect(callCount("onboarding_get_model_options")).toBe(1);

		// Step landed; content state + loading wait for the batch.
		expect(result.current.step?.step_name).toBe("Microphone");
		expect(result.current.loading).toBe(true);
		expect(result.current.hotkeyPresets).toEqual([]);
		expect(result.current.microphones).toEqual([]);

		// Resolve the whole batch → state lands, loading clears.
		await act(async () => {
			controls.config.resolve(
				makeConfig({
					hotkey: "<ctrl>+<shift>+v",
					microphone: "mic-2",
				}) as VoiceTyperConfig,
			);
			controls.mics.resolve({ microphones: MIC_LIST });
			controls.presets.resolve({ presets: ["<f2>", "<caps_lock>"] });
			controls.models.resolve({ models: [] });
		});
		await flush();

		expect(result.current.loading).toBe(false);
		expect(result.current.initError).toBeNull();
		expect(result.current.hotkeyPresets).toEqual(["<f2>", "<caps_lock>"]);
		expect(result.current.microphones).toEqual(MIC_LIST);
		// Config prefill applied BEFORE the mic reconciliation — the
		// config-restored mic (present in the list) survives it.
		expect(result.current.selectedHotkey).toBe("<ctrl>+<shift>+v");
		expect(result.current.selectedMic).toBe("mic-2");
	});

	it("mic reconciliation replaces a config-restored mic that is no longer present", async () => {
		const controls = wireControls();
		const { result } = renderHook(() => useOnboardingWizard());
		await flush();
		await act(async () => {
			controls.start.resolve({ step: 0, total_steps: 6, step_name: "Welcome" });
		});
		await flush();
		await act(async () => {
			controls.config.resolve(
				makeConfig({ microphone: "gone-mic" }) as VoiceTyperConfig,
			);
			controls.mics.resolve({ microphones: MIC_LIST });
			controls.presets.resolve({ presets: [] });
			controls.models.resolve({ models: [] });
		});
		await flush();

		// "gone-mic" is not in the list → the reconciliation falls back to
		// the OS-default mic.
		expect(result.current.selectedMic).toBe("mic-1");
		expect(result.current.loading).toBe(false);
	});

	it("get_config failure is non-fatal — the wizard continues without prefill", async () => {
		const controls = wireControls();
		const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
		const { result } = renderHook(() => useOnboardingWizard());
		await flush();
		await act(async () => {
			controls.start.resolve({ step: 0, total_steps: 6, step_name: "Welcome" });
		});
		await flush();
		await act(async () => {
			controls.config.reject(new Error("probe failed"));
			controls.mics.resolve({ microphones: MIC_LIST });
			controls.presets.resolve({ presets: ["<f2>"] });
			controls.models.resolve({ models: [] });
		});
		await flush();

		expect(result.current.loading).toBe(false);
		expect(result.current.initError).toBeNull();
		expect(result.current.hotkeyPresets).toEqual(["<f2>"]);
		expect(result.current.microphones).toEqual(MIC_LIST);
		// No prefill → the default mic was selected by the reconciliation.
		expect(result.current.selectedMic).toBe("mic-1");
		expect(warnSpy).toHaveBeenCalled();
		warnSpy.mockRestore();
	});

	it("a content-fetch failure surfaces as initError (error path preserved)", async () => {
		const controls = wireControls();
		const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
		const { result } = renderHook(() => useOnboardingWizard());
		await flush();
		await act(async () => {
			controls.start.resolve({ step: 0, total_steps: 6, step_name: "Welcome" });
		});
		await flush();
		await act(async () => {
			controls.config.resolve(makeConfig({}) as VoiceTyperConfig);
			controls.mics.reject(new Error("mic enumeration failed"));
			controls.presets.resolve({ presets: [] });
			controls.models.resolve({ models: [] });
		});
		await flush();

		expect(result.current.loading).toBe(false);
		expect(result.current.initError).toBe("mic enumeration failed");
		errorSpy.mockRestore();
	});

	it("a content-fetch failure KEEPS the already-fetched config prefill", async () => {
		const controls = wireControls();
		const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
		const { result } = renderHook(() => useOnboardingWizard());
		await flush();
		await act(async () => {
			controls.start.resolve({ step: 0, total_steps: 6, step_name: "Welcome" });
		});
		await flush();
		await act(async () => {
			// get_config succeeds with saved selections; the mic fetch fails.
			// The pre-parallel sequential code had applied the prefill before
			// the mic round-trip failed — the parallel batch must preserve
			// that: initError set AND prefill applied (Promise.all discarded
			// the config the moment the sibling fetch rejected).
			controls.config.resolve(
				makeConfig({
					hotkey: "<ctrl>+<shift>+v",
					microphone: "mic-2",
					model_size: "large-v3",
					huggingface_consent: true,
				}) as VoiceTyperConfig,
			);
			controls.mics.reject(new Error("mic enumeration failed"));
			controls.presets.resolve({ presets: [] });
			controls.models.resolve({ models: [] });
		});
		await flush();

		// The failure still surfaces as initError exactly as before…
		expect(result.current.loading).toBe(false);
		expect(result.current.initError).toBe("mic enumeration failed");
		// …AND the fetched config prefill survives onto the error screen.
		expect(result.current.selectedHotkey).toBe("<ctrl>+<shift>+v");
		expect(result.current.selectedMic).toBe("mic-2");
		expect(result.current.selectedModel).toBe("large-v3");
		// The mic list never landed (its fetch failed) — no reconciliation,
		// so the config-restored mic selection stays as-is.
		expect(result.current.microphones).toEqual([]);
		expect(result.current.hfConsent).toBe(true);
		errorSpy.mockRestore();
	});

	it("a LATE content-fetch failure keeps everything applied before it", async () => {
		const controls = wireControls();
		const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
		const { result } = renderHook(() => useOnboardingWizard());
		await flush();
		await act(async () => {
			controls.start.resolve({ step: 0, total_steps: 6, step_name: "Welcome" });
		});
		await flush();
		await act(async () => {
			controls.config.resolve(
				makeConfig({
					hotkey: "<ctrl>+<shift>+v",
					microphone: "mic-2",
				}) as VoiceTyperConfig,
			);
			controls.mics.resolve({ microphones: MIC_LIST });
			controls.presets.resolve({ presets: ["<f2>"] });
			controls.models.reject(new Error("model catalog failed"));
		});
		await flush();

		// The models failure surfaces as initError…
		expect(result.current.loading).toBe(false);
		expect(result.current.initError).toBe("model catalog failed");
		// …while every fetch applied before it (in the original apply
		// order: config prefill → mic reconciliation → presets) stays
		// applied — the pre-parallel sequential behaviour.
		expect(result.current.selectedHotkey).toBe("<ctrl>+<shift>+v");
		// mic-2 is present in MIC_LIST, so the reconciliation's "keep
		// prev" check retains the config-restored selection.
		expect(result.current.selectedMic).toBe("mic-2");
		expect(result.current.microphones).toEqual(MIC_LIST);
		expect(result.current.hotkeyPresets).toEqual(["<f2>"]);
		// The models list never landed (its fetch failed).
		expect(result.current.modelOptions).toEqual([]);
		errorSpy.mockRestore();
	});

	it("resolves all state when every fetch resolves in reverse order", async () => {
		const controls = wireControls();
		const { result } = renderHook(() => useOnboardingWizard());
		await flush();
		await act(async () => {
			controls.start.resolve({ step: 5, total_steps: 6, step_name: "Done" });
		});
		await flush();
		// Resolve in REVERSE order — Promise.all must still settle.
		await act(async () => {
			controls.models.resolve({ models: [] });
			controls.presets.resolve({ presets: ["<f2>"] });
			controls.mics.resolve({ microphones: [] });
			controls.config.resolve(makeConfig({}) as VoiceTyperConfig);
		});
		await waitFor(() => {
			expect(result.current.loading).toBe(false);
		});
		expect(result.current.initError).toBeNull();
	});
});
