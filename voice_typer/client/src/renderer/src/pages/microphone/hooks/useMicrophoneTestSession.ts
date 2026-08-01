// Test-session state machine hook for the Microphone page.
//
//Extracted from the former ``useMicrophoneTest`` monolith ().
// Owns the test-recording lifecycle state
// (``testRunning`` / ``testCountdown`` / ``testElapsed`` /
// ``testAudioBase64`` / ``rawAudioBase64`` / ``testDurationMs`` /
// ``testQuality`` / ``filtersSinceLastTest``) plus the countdown +
// elapsed timers and the ``microphone_test_complete`` push-event
// subscription that drives ``stopTest`` when the backend finishes
// recording.
//
//(1-C Finding 8): ``startTest`` / ``stopTest`` /
// ``selectMicrophone`` are wrapped in ``useCallback`` with their actual
// deps so the ``microphone_test_complete`` subscription and the
// countdown timer can capture them directly — the ``stopTestRef``
// indirection is no longer needed.
//
// Inputs from sibling hooks:
// - ``setLevel`` / ``setPeak`` / ``setMicMonitoring`` (owned by
//   ``useMicrophoneLevelMonitor``) — used to reset the meter on test
//   start / stop / mic-change.
// - ``stopPlayback`` (owned by ``useMicrophonePlayback``) — called at
//   the start of ``startTest`` to pause any playing test audio (the
//   prior implementation relied on the unmount-cleanup effect pausing
//   audio on the ``testRunning`` transition; that effect now lives in
//   the playback hook and fires only on unmount, so we pause
//   explicitly here to preserve the behaviour).
// - ``selectMicrophoneRef`` (owned by the page, shared with
//   ``useMicrophoneData``) — assigned the latest stable
//   ``selectMicrophone`` closure so the data hook's
//   ``microphones_changed`` hot-swap handler can invoke it.

import {
	type Dispatch,
	type MutableRefObject,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import type { PythonCall } from "@/hooks/usePython";
import { usePythonEvent } from "@/hooks/usePython";
import type { SnackbarType } from "@/hooks/useSnackbar";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
import { buildTestFilters } from "../lib/buildTestFilters";
import { computeAudioKey } from "../lib/computeAudioKey";
import type { TestResultQuality, TestStopResult } from "../lib/types";

/** Type of the ``t()`` i18n function — accepts a key + optional params. */
type TFunction = (key: string, params?: Record<string, string>) => string;

/** Type of the ``showSnack`` toast function. */
type ShowSnack = (message: string, type?: SnackbarType) => void;

interface UseMicrophoneTestSessionOptions {
	/** ``call`` from ``usePython()`` — passed in so the composition hook owns the single bridge subscription. */
	call: PythonCall;
	/** Current voice-typer config. */
	config: VoiceTyperConfig | null;
	/** Available microphones (used for the "Using mic X" snackbar label). */
	microphones: MicrophoneDevice[];
	/** Config setter (used by ``selectMicrophone`` for the optimistic update). */
	setConfig: Dispatch<SetStateAction<VoiceTyperConfig | null>>;
	/** Config updater — kept for parity with the prior signature; not used directly here. */
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
	/** Snackbar toaster (passed in so the hook is testable without the React context). */
	showSnack: ShowSnack;
	/** i18n ``t`` function (passed in for testability). */
	t: TFunction;
	/** User-configurable test recording duration (3–30s). */
	testDurationSec: number;
	/** Level setter from ``useMicrophoneLevelMonitor``. */
	setLevel: Dispatch<SetStateAction<number>>;
	/** Peak setter from ``useMicrophoneLevelMonitor``. */
	setPeak: Dispatch<SetStateAction<number>>;
	/** micMonitoring setter from ``useMicrophoneLevelMonitor``. */
	setMicMonitoring: Dispatch<SetStateAction<boolean>>;
	/** ``stopPlayback`` from ``useMicrophonePlayback`` — called at startTest to pause any playing audio. */
	stopPlayback: () => void;
	/**
	 * Ref-to-latest-``testRunning`` flag owned by the composition hook
	 * and shared with ``useMicrophoneLevelMonitor`` so its ``mic_level``
	 * push handler can gate updates on ``testRunning || micMonitoring``
	 * without rebinding on every render. This hook syncs it via an
	 * effect whenever ``testRunning`` changes.
	 */
	testRunningRef: MutableRefObject<boolean>;
	/**
	 * Optional ref-to-latest-``selectMicrophone`` owned by the page,
	 * shared with ``useMicrophoneData`` so the
	 * ``microphones_changed`` hot-swap handler can invoke the latest
	 * closure. Assigned via an effect (not on every render) now that
	 * ``selectMicrophone`` is ``useCallback``-stable.
	 */
	selectMicrophoneRef?: MutableRefObject<
		(micId: string | null) => Promise<void>
	>;
}

export interface UseMicrophoneTestSessionResult {
	testRunning: boolean;
	testCountdown: number;
	testElapsed: number;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	testDurationMs: number;
	testQuality: TestResultQuality | null;
	filtersSinceLastTest: string;
	startTest: () => Promise<void>;
	stopTest: () => Promise<void>;
	selectMicrophone: (micId: string | null) => Promise<void>;
}

export function useMicrophoneTestSession({
	call,
	config,
	microphones,
	setConfig,
	updateConfig,
	showSnack,
	t,
	testDurationSec,
	setLevel,
	setPeak,
	setMicMonitoring,
	stopPlayback,
	testRunningRef,
	selectMicrophoneRef,
}: UseMicrophoneTestSessionOptions): UseMicrophoneTestSessionResult {
	// ``updateConfig`` is part of the public session-hook signature
	// for parity with the prior ``useMicrophoneTest`` API but is not
	// used directly here — preset / config-change handlers live in
	// the composition hook. Reference it to satisfy exhaustive-deps
	// lint without making it a runtime dep.
	void updateConfig;

	const [testRunning, setTestRunning] = useState(false);
	const [testCountdown, setTestCountdown] = useState(0);
	const [testElapsed, setTestElapsed] = useState(0);
	const [testAudioBase64, setTestAudioBase64] = useState<string | null>(null);
	const [rawAudioBase64, setRawAudioBase64] = useState<string | null>(null);
	const [testDurationMs, setTestDurationMs] = useState(0);
	const [testQuality, setTestQuality] = useState<TestResultQuality | null>(
		null,
	);
	// Tracks whether filters have changed since last test (invalidation).
	const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>("");

	const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const stoppingRef = useRef(false);
	//``testRunningRef`` is owned by the composition hook so the
	// level monitor (declared alongside this hook in the composition)
	// can read it without an ordering dependency. This hook syncs it
	// via the effect below whenever ``testRunning`` changes.
	useEffect(() => {
		testRunningRef.current = testRunning;
	}, [testRunning, testRunningRef]);

	const stopTest = useCallback(async () => {
		if (stoppingRef.current) return;
		stoppingRef.current = true;

		setTestRunning(false);
		if (testTimerRef.current) {
			clearInterval(testTimerRef.current);
			testTimerRef.current = null;
		}
		if (elapsedTimerRef.current) {
			clearInterval(elapsedTimerRef.current);
			elapsedTimerRef.current = null;
		}
		setLevel(0);
		setTestCountdown(0);

		try {
			const result = await call<TestStopResult>("microphone_test_stop");

			if (result?.success && result?.audio_base64) {
				setTestAudioBase64(result.audio_base64);
				setRawAudioBase64(result.raw_audio_base64 || null);
				setTestDurationMs(result.duration_ms || 0);
				if (result.quality) {
					setTestQuality(result.quality);
				}
				showSnack(
					t("microphone.recorded", {
						seconds: (result.duration_ms / 1000).toFixed(1),
					}),
					"success",
				);
			} else if (result?.success) {
				let msg = t("microphone.noAudio");
				const activeMicId = config?.microphone ?? null;
				if (activeMicId !== null) {
					msg += t("microphone.tryDefaultMic");
				}
				showSnack(msg, "warning");
			} else {
				showSnack(result?.message ?? t("microphone.testFailed"), "error");
			}
		} catch (err) {
			console.error("Failed to stop microphone test:", err);
			showSnack(t("microphone.stopTestFailed"), "error");
		} finally {
			stoppingRef.current = false;
		}
	}, [call, config, showSnack, t, setLevel]);

	const startTest = useCallback(async () => {
		setTestAudioBase64(null);
		setRawAudioBase64(null);
		setTestDurationMs(0);
		setTestQuality(null);
		setLevel(0);
		setPeak(0);
		setTestElapsed(0);

		// Pause any playing test audio before starting a new
		// recording (preserves the prior behaviour where the
		// ``testRunning``-transition cleanup paused the audio
		// element). The playback hook's own unmount cleanup
		// only fires on unmount, so we pause explicitly here.
		stopPlayback();

		const micId = config?.microphone ?? null;

		// Record the current filter state for invalidation tracking.
		setFiltersSinceLastTest(computeAudioKey(config));

		try {
			const result = await call<{
				success: boolean;
				message: string;
				duration: number;
				sample_rate: number;
			}>("microphone_test_start", {
				mic_id: micId,
				duration: testDurationSec,
				filters: buildTestFilters(config),
			});

			if (!result?.success) {
				showSnack(result?.message ?? t("microphone.startTestFailed"), "error");
				return;
			}

			setTestRunning(true);
			setTestCountdown(Math.ceil(result.duration || testDurationSec));

			// Timer countdown
			if (testTimerRef.current) clearInterval(testTimerRef.current);
			const startTime = Date.now();
			const totalDurationMs = (result.duration || testDurationSec) * 1000;
			const checkInterval = setInterval(() => {
				const elapsed = Date.now() - startTime;
				const remaining = Math.max(
					0,
					Math.ceil((totalDurationMs - elapsed) / 1000),
				);
				setTestCountdown(remaining);

				if (remaining <= 0) {
					clearInterval(checkInterval);
					if (checkInterval === testTimerRef.current) {
						testTimerRef.current = null;
					}
					void stopTest();
				}
			}, 500);
			testTimerRef.current = checkInterval;

			// Elapsed timer for the 00:03 / 00:10 display
			if (elapsedTimerRef.current) clearInterval(elapsedTimerRef.current);
			const elapsedInterval = setInterval(() => {
				const elapsed = Date.now() - startTime;
				setTestElapsed(Math.floor(elapsed / 1000));
			}, 200);
			elapsedTimerRef.current = elapsedInterval;
		} catch (err) {
			console.error("Failed to start microphone test:", err);
			showSnack(t("microphone.startTestFailed"), "error");
		}
	}, [
		call,
		config,
		showSnack,
		t,
		testDurationSec,
		stopPlayback,
		stopTest,
		setLevel,
		setPeak,
	]);

	const selectMicrophone = useCallback(
		async (micId: string | null) => {
			// Stop any active test first
			if (testRunningRef.current && !stoppingRef.current) {
				try {
					await call("microphone_test_cancel");
				} catch (e) {
					/* ignore — test may have already finished, or the
					   backend may be tearing down */
					console.warn(
						"[useMicrophoneTestSession] selectMicrophone cancel failed:",
						e,
					);
				}
				setTestRunning(false);
				setTestAudioBase64(null);
				setRawAudioBase64(null);
				setTestQuality(null);
				if (testTimerRef.current) {
					clearInterval(testTimerRef.current);
					testTimerRef.current = null;
				}
				if (elapsedTimerRef.current) {
					clearInterval(elapsedTimerRef.current);
					elapsedTimerRef.current = null;
				}
			}

			setTestAudioBase64(null);
			setRawAudioBase64(null);
			setTestQuality(null);

			try {
				await call("set_config", { microphone: micId });
				setConfig((prev) => (prev ? { ...prev, microphone: micId } : prev));
				setLevel(0);
				setPeak(0);
				setMicMonitoring(false);
				call("level_monitor_start", { mic_id: micId }).catch((err) =>
					console.warn(
						"[IPC] microphone command failed: level_monitor_start:",
						err,
					),
				);
				const label =
					micId === null
						? t("microphone.systemDefault")
						: (microphones.find((m) => (m.id ?? String(m.index)) === micId)
								?.name ?? t("microphone.microphone"));
				showSnack(t("microphone.usingMic", { name: label }), "success");
			} catch {
				showSnack(t("microphone.setFailed"), "error");
			}
		},
		[
			call,
			microphones,
			setConfig,
			showSnack,
			t,
			setLevel,
			setPeak,
			setMicMonitoring,
			testRunningRef,
		],
	);

	// When the backend finishes recording, drive ``stopTest`` to fetch
	// the result + reset the test-running UI. Now that ``stopTest`` is
	// ``useCallback``-stable, we capture it directly (no ``stopTestRef``
	// indirection). The subscription re-binds when ``testRunning`` OR
	// ``stopTest`` changes — ``stopTest`` changes are bounded by its
	// deps (``call`` / ``config`` / ``showSnack`` / ``t``), so this is
	// cheap and equivalent to the prior ref-to-latest pattern.
	usePythonEvent(
		"microphone_test_complete",
		useCallback(
			(_data: unknown): (() => void) | undefined => {
				if (testRunning && !stoppingRef.current) {
					void stopTest();
				}
				return undefined;
			},
			[testRunning, stopTest],
		),
	);

	// Unmount / testRunning-transition cleanup: clear the countdown +
	// elapsed intervals and cancel an in-flight test recording so the
	// backend doesn't keep the mic stream open after the user
	// navigates away. Audio-pausing on unmount is owned by
	// ``useMicrophonePlayback`` (its own cleanup effect).
	useEffect(() => {
		return () => {
			if (testTimerRef.current) {
				clearInterval(testTimerRef.current);
				testTimerRef.current = null;
			}
			if (elapsedTimerRef.current) {
				clearInterval(elapsedTimerRef.current);
				elapsedTimerRef.current = null;
			}
			if (testRunning && !stoppingRef.current) {
				call("microphone_test_cancel").catch((err) =>
					console.warn(
						"[IPC] microphone command failed: microphone_test_cancel:",
						err,
					),
				);
			}
		};
	}, [call, testRunning]);

	// Keep ``selectMicrophoneRef`` pointed at the latest stable
	//``selectMicrophone`` closure (). The assignment now happens
	// via an effect with ``[selectMicrophone]`` deps instead of on
	// every render — now that ``selectMicrophone`` is
	// ``useCallback``-stable, the assignment runs only when its deps
	// change (``call`` / ``microphones`` / ``setConfig`` / ``showSnack``
	// / ``t`` / level setters), not on every render.
	useEffect(() => {
		if (selectMicrophoneRef) {
			selectMicrophoneRef.current = selectMicrophone;
		}
	}, [selectMicrophone, selectMicrophoneRef]);

	return {
		testRunning,
		testCountdown,
		testElapsed,
		testAudioBase64,
		rawAudioBase64,
		testDurationMs,
		testQuality,
		filtersSinceLastTest,
		startTest,
		stopTest,
		selectMicrophone,
	};
}
