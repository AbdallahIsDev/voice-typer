// Test-session state machine hook for the Microphone page.
//
// Owns the test-recording lifecycle state
// (``testRunning`` / ``testElapsed`` /
// ``testAudioBase64`` / ``rawAudioBase64`` / ``testDurationMs`` /
// ``testQuality`` / ``filtersSinceLastTest``) plus the countdown +
// elapsed timers and the ``microphone_test_complete`` push-event
// subscription that drives ``stopTest`` when the backend finishes
// recording.
//
//(1-C Finding 8): ``startTest`` / ``stopTest`` /
// ``selectMicrophone`` are wrapped in ``useCallback`` with their actual
// countdown timer can capture them directly, the ``stopTestRef``
// indirection is no longer needed.
//
// Inputs from sibling hooks:
// - ``setLevel`` / ``setPeak`` / ``setMicMonitoring`` (owned by
//   ``useMicrophoneLevelMonitor``), used to reset the meter on test
//   start / stop / mic-change.
// - ``stopPlayback`` (owned by ``useMicrophonePlayback``), called at
//   prior implementation relied on the unmount-cleanup effect pausing
//   audio on the ``testRunning`` transition; that effect now lives in
//   explicitly here to preserve the behaviour).
// - ``selectMicrophoneRef`` (owned by the page, shared with
//   ``useMicrophoneData``), assigned the latest stable
//   ``selectMicrophone`` closure so the data hook's
//   ``microphones_changed`` hot-swap handler can invoke it.

import {
	type Dispatch,
	type RefObject,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { useLatestRef } from "@/hooks/useLatestRef";
import type { PythonCall } from "@/hooks/usePython";
import { usePythonEvent } from "@/hooks/usePython";
import type { ShowSnackOptions, SnackbarType } from "@/hooks/useSnackbar";
import {
	CONSENT_REQUIRED_CODE,
	VOICE_BIOMETRIC_CONSENT_FIELD,
} from "@/lib/consent";
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import { userFacingErrorMessage } from "@/lib/errors/userFacingErrorMessage";
import type { LausuConfig, MicrophoneDevice } from "@/types/config";
import { buildTestFilters } from "../lib/buildTestFilters";
import { computeAudioKey } from "../lib/computeAudioKey";
import { fetchTestAudioFileDeduped } from "../lib/testAudioTransfer";
import {
	_resetMicrophoneTestCache,
	readTestSessionCache,
	writeTestSessionCache,
} from "../lib/testSessionCache";
import type { TestResultQuality, TestStopResult } from "../lib/types";

export { _resetMicrophoneTestCache };

/**
 * Fixed microphone-test recording duration, in seconds. The test is
 * permanently 10 seconds, there is no user-facing configurability.
 * Single source of truth for the ``microphone_test_start`` payload and
 * the countdown fallback; the backend independently clamps to
 * [1.0, 60.0].
 */
export const MICROPHONE_TEST_DURATION_SEC = 10;

/**
 * Mic-test recording UI TTL, in milliseconds. 5 minutes after a mic
 * test completes, the recording is cleared from the UI too (silent
 * expiry). Keep in sync with the backend MIC_TEST_RECORDING_TTL_SEC
 * (both 5 min, changed together) — disk side handled separately on
 * the backend.
 */
export const MIC_TEST_RECORDING_TTL_MS = 5 * 60 * 1000;

// Module-level last-test cache lives in ../lib/testSessionCache (with
// keeps only the subscription/state/timer/effect wiring.

/** Type of the ``t()`` i18n function, accepts a key + optional params. */
type TFunction = (key: string, params?: Record<string, string>) => string;

/** Type of the ``showSnack`` toast function (matches ``useSnackbar``). */
type ShowSnack = (
	message: string,
	type?: SnackbarType,
	options?: ShowSnackOptions,
) => void;

interface UseMicrophoneTestSessionOptions {
	/** ``call`` from ``usePython()``, passed in so the composition hook owns the single bridge subscription. */
	call: PythonCall;
	/** Current lausu config. */
	config: LausuConfig | null;
	/** Available microphones (used for the "Using mic X" snackbar label). */
	microphones: MicrophoneDevice[];
	/** Config setter (used by ``selectMicrophone`` for the optimistic update). */
	setConfig: Dispatch<SetStateAction<LausuConfig | null>>;
	/** Config updater, kept for parity with the prior signature; not used directly here. */
	updateConfig: (updates: Partial<LausuConfig>) => void;
	/** Snackbar toaster (passed in so the hook is testable without the React context). */
	showSnack: ShowSnack;
	/** i18n ``t`` function (passed in for testability). */
	t: TFunction;
	/** Level setter from ``useMicrophoneLevelMonitor``. */
	setLevel: Dispatch<SetStateAction<number>>;
	/** Peak setter from ``useMicrophoneLevelMonitor``. */
	setPeak: Dispatch<SetStateAction<number>>;
	/** micMonitoring setter from ``useMicrophoneLevelMonitor``. */
	setMicMonitoring: Dispatch<SetStateAction<boolean>>;
	/** ``stopPlayback`` from ``useMicrophonePlayback``, called at startTest to pause any playing audio. */
	stopPlayback: () => void;
	/**
	 * Ref-to-latest-``testRunning`` flag owned by the composition hook
	 * and shared with ``useMicrophoneLevelMonitor`` so its ``mic_level``
	 * push handler can gate updates on ``testRunning || micMonitoring``
	 * without rebinding on every render. This hook syncs it via an
	 * effect whenever ``testRunning`` changes.
	 */
	testRunningRef: RefObject<boolean>;
	/**
	 * Optional ref-to-latest-``selectMicrophone`` owned by the page,
	 * shared with ``useMicrophoneData`` so the
	 * ``microphones_changed`` hot-swap handler can invoke the latest
	 * closure. Assigned via an effect (not on every render) now that
	 * ``selectMicrophone`` is ``useCallback``-stable.
	 */
	selectMicrophoneRef?: RefObject<(micId: string | null) => Promise<void>>;
	// point-of-use consent dialog (ConsentGateDialog), the dialog's
	// "Open Settings" action navigates itself via the consentGate
	// store, so the per-hook callback was dead code. See
	// consentGate.ts.
}

export interface UseMicrophoneTestSessionResult {
	testRunning: boolean;
	/** True while the start-test IPC is in flight (Start disabled, no recording UI yet). */
	testStarting: boolean;
	testElapsed: number;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	testDurationMs: number;
	testQuality: TestResultQuality | null;
	/** Best-effort auto-transcription from the last test (backend-provided). */
	testTranscription: string | null;
	/** True when the backend could not transcribe the last test recording. */
	testTranscriptionUnavailable: boolean;
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
	setLevel,
	setPeak,
	setMicMonitoring,
	stopPlayback,
	testRunningRef,
	selectMicrophoneRef,
}: UseMicrophoneTestSessionOptions): UseMicrophoneTestSessionResult {
	// callRef mirror (Home.tsx pattern): the unmount/transition cleanup
	// effect below must not depend on the `call` identity, a test mock
	// handing out a fresh `call` per render would re-fire it (OOM loop
	// class). ``callRef.current`` is read at cleanup time instead.
	const callRef = useLatestRef(call);
	// stopPlayback mirror (same pattern): the silent 5-min expiry timeout
	// `stopPlaybackRef.current` is read at fire time instead.
	const stopPlaybackRef = useLatestRef(stopPlayback);
	// ``updateConfig`` is part of the public session-hook signature
	// used directly here, preset / config-change handlers live in
	// lint without making it a runtime dep.
	void updateConfig;

	const [testRunning, setTestRunning] = useState(false);
	// Guard against double-start while the start IPC is in flight. This
	// is UI-only (disables Start + ignores re-entry), it never drives
	// backend confirms success.
	const [testStarting, setTestStarting] = useState(false);
	const [testElapsed, setTestElapsed] = useState(0);
	// Seed the per-test React state from the module-level
	// cache so navigating away from the Microphone page and back
	// restores the last test's recording + verdict. The cache is
	// invalidated on startTest (a new test) and on selectMicrophone
	// (mic switch, the cached recording is for a different mic).
	const initialSession = readTestSessionCache();
	const [testAudioBase64, setTestAudioBase64] = useState<string | null>(
		initialSession.audioBase64,
	);
	const [rawAudioBase64, setRawAudioBase64] = useState<string | null>(
		initialSession.rawAudioBase64,
	);
	const [testDurationMs, setTestDurationMs] = useState(
		initialSession.durationMs,
	);
	const [testQuality, setTestQuality] = useState<TestResultQuality | null>(
		initialSession.quality,
	);
	const [testTranscription, setTestTranscription] = useState<string | null>(
		initialSession.transcription,
	);
	const [testTranscriptionUnavailable, setTestTranscriptionUnavailable] =
		useState(initialSession.transcriptionUnavailable);
	// Tracks whether filters have changed since last test (invalidation).
	const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>("");

	const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const stoppingRef = useRef(false);
	const startingRef = useRef(false);
	// Silent 5-min UI expiry of the mic-test recording
	// (MIC_TEST_RECORDING_TTL_MS). Generation-guarded: arming bumps
	// an older test's timer can never clear a newer test. Invalidate
	// (gen++ + clearTimeout) at startTest / selectMicrophone / unmount.
	const expiryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const expiryGenRef = useRef(0);
	// INTERNAL lifecycle flag owned by THIS hook (synchronous). The
	// level monitor, but the unmount cleanup must not depend on a prop-ref's
	// identity staying stable across renders, read our own flag instead.
	const recordingActiveRef = useRef(false);
	// Latest ``startTest`` closure, so the consent dialog's retry
	// itself while it's being defined.
	const startTestRef = useRef<() => Promise<void>>(async () => {});
	// level monitor (declared alongside this hook in the composition)
	// can read it without an ordering dependency. This hook syncs it
	// via the effect below whenever ``testRunning`` changes.
	useEffect(() => {
		testRunningRef.current = testRunning;
	}, [testRunning, testRunningRef]);

	const stopTest = useCallback(async () => {
		if (stoppingRef.current) return;
		stoppingRef.current = true;
		recordingActiveRef.current = false;

		setTestRunning(false);
		if (testTimerRef.current) {
			clearInterval(testTimerRef.current);
			testTimerRef.current = null;
		}
		setLevel(0);

		try {
			const result = await call<TestStopResult>("microphone_test_stop");

			const audioRef = result?.success ? result.audio_file : null;
			if (result?.success && audioRef?.path) {
				// File-reference transport: fetch the persisted WAVs chunked
				// (each IPC frame < 1 MiB), assemble base64. A fetch failure
				// must not discard the valid recording verdict below.
				let audioB64: string | null = null;
				let rawB64: string | null = null;
				try {
					[audioB64, rawB64] = await Promise.all([
						fetchTestAudioFileDeduped(call, audioRef.path),
						result.raw_audio_file?.path
							? fetchTestAudioFileDeduped(call, result.raw_audio_file.path)
							: Promise.resolve(null),
					]);
					setTestAudioBase64(audioB64);
					setRawAudioBase64(rawB64);
				} catch (fetchErr) {
					console.error(
						"[renderer:useMicrophoneTestSession] Failed to fetch test audio:",
						fetchErr,
					);
				}
				// The recording itself succeeded, duration/quality verdict
				// failed above (only the playable data is missing).
				setTestDurationMs(result.duration_ms || 0);
				if (result.quality) {
					setTestQuality(result.quality);
				}
				const transcriptionText =
					typeof result.transcription === "string"
						? result.transcription
						: null;
				const transcriptionUnavailable =
					result.transcription_unavailable === true;
				setTestTranscription(transcriptionText);
				setTestTranscriptionUnavailable(transcriptionUnavailable);
				// discard it.
				writeTestSessionCache({
					audioBase64: audioB64,
					rawAudioBase64: rawB64,
					durationMs: result.duration_ms || 0,
					// Keep any prior quality when the backend omitted it
					quality: result.quality ?? readTestSessionCache().quality,
					transcription: transcriptionText,
					transcriptionUnavailable,
				});
				// fetch-failed-null above: the branch is entered whenever
				// newer test is never cleared by an older timer. Firing is
				// a playing data-URI player), clears ALL test state + cache.
				expiryGenRef.current += 1;
				const expiryGen = expiryGenRef.current;
				if (expiryTimerRef.current) {
					clearTimeout(expiryTimerRef.current);
					expiryTimerRef.current = null;
				}
				expiryTimerRef.current = setTimeout(() => {
					if (expiryGenRef.current !== expiryGen) return;
					expiryTimerRef.current = null;
					stopPlaybackRef.current();
					setTestAudioBase64(null);
					setRawAudioBase64(null);
					setTestDurationMs(0);
					setTestQuality(null);
					setTestTranscription(null);
					setTestTranscriptionUnavailable(false);
					_resetMicrophoneTestCache();
				}, MIC_TEST_RECORDING_TTL_MS);
				showSnack(
					t("microphone.recorded", {
						seconds: (result.duration_ms / 1000).toFixed(1),
					}),
					"success",
				);
			} else if (
				result?.success === false &&
				typeof result.message === "string" &&
				/no test running/i.test(result.message)
			) {
				// Benign stale-trigger no-op: the backend already finalized this
				// recording (auto-stop raced a manual stop, or a lost-push safety
				// NOT a failure; toasting it trained users to ignore real errors.
				return;
			} else if (result?.success) {
				let msg = t("microphone.noAudio");
				const activeMicId = config?.microphone ?? null;
				if (activeMicId !== null) {
					msg = `${msg} ${t("microphone.tryDefaultMic")}`;
				}
				showSnack(msg, "warning");
			} else {
				showSnack(result?.message ?? t("microphone.testFailed"), "error");
			}
		} catch (err) {
			console.error(
				"[renderer:useMicrophoneTestSession] Failed to stop microphone test:",
				err,
			);
			// Known failure classes (timeout / backend unreachable /
			// rate limit) get their curated localized message; unknown
			// ones keep the contextual "failed to stop" fallback.
			showSnack(
				userFacingErrorMessage(err, t, t("microphone.stopTestFailed")),
				"error",
			);
		} finally {
			stoppingRef.current = false;
		}
	}, [call, config, showSnack, t, setLevel, stopPlaybackRef]);

	const startTest = useCallback(async () => {
		// Invalidate any pending silent-expiry timer: a newer test must
		// never be cleared by an older test's timer.
		expiryGenRef.current += 1;
		if (expiryTimerRef.current) {
			clearTimeout(expiryTimerRef.current);
			expiryTimerRef.current = null;
		}
		if (startingRef.current) return;
		startingRef.current = true;
		setTestStarting(true);
		setTestAudioBase64(null);
		setRawAudioBase64(null);
		setTestDurationMs(0);
		setTestQuality(null);
		setTestTranscription(null);
		setTestTranscriptionUnavailable(false);
		// Invalidate the module-level test cache when a
		// recording, which is now superseded. The setX calls above
		// test doesn't surface stale data on return.
		_resetMicrophoneTestCache();
		setLevel(0);
		setPeak(0);
		setTestElapsed(0);

		// Pause any playing test audio before starting a new
		// ``testRunning``-transition cleanup paused the audio
		// element). The playback hook's own unmount cleanup
		// only fires on unmount, so we pause explicitly here.
		stopPlayback();

		const micId = config?.microphone ?? null;

		// Record the current filter state for invalidation tracking.
		setFiltersSinceLastTest(computeAudioKey(config));

		// resolved-envelope + thrown-error paths below, and shared with
		// (Allow → persists the consent → retries the full test start
		// exact toggle). The ``consentField`` from the backend
		// older backends whose plain ``success:false`` envelope omits
		// it).
		const showConsentSnack = (consentField: string) => {
			openConsentGate({
				consentField,
				bodyKey: consentBodyKey(consentField),
				// session state (countdown / timers / running flag) was
				// set up, so a raw IPC retry would leave the UI in a
				// half-started state.
				onAllow: () => startTestRef.current(),
			});
		};

		try {
			const result = await call<{
				success: boolean;
				message: string;
				duration: number;
				sample_rate: number;
			}>("microphone_test_start", {
				mic_id: micId,
				duration: MICROPHONE_TEST_DURATION_SEC,
				filters: buildTestFilters(config),
			});

			if (!result?.success) {
				// The backend's ``client.consent_required`` envelope
				// mapping) carries a structured ``code`` field and a
				// code first (robust to message rewording), falling back
				// consent requirement with a deep-link to Settings →
				// Privacy instead of the generic failure toast. The
				// handler docstring (see microphone_test_handlers.py)
				// explicitly designs this envelope so the renderer can
				// do exactly this.
				const resolvedCode = (result as { code?: unknown } | null)?.code;
				if (
					resolvedCode === CONSENT_REQUIRED_CODE ||
					(typeof result?.message === "string" &&
						result.message.includes("consent required"))
				) {
					// envelope so the deep-link lands on the EXACT
					// Settings toggle (not just the Privacy tab).
					const resolvedConsentField = (
						result as { consent_field?: unknown } | null
					)?.consent_field;
					showConsentSnack(
						typeof resolvedConsentField === "string"
							? resolvedConsentField
							: VOICE_BIOMETRIC_CONSENT_FIELD,
					);
					return;
				}
				showSnack(result?.message ?? t("microphone.startTestFailed"), "error");
				return;
			}

			setTestRunning(true);
			recordingActiveRef.current = true;
			setTestElapsed(0);

			// SINGLE lifecycle-synced timer. Drives BOTH the visible
			// "Recording... 00:NN" readout AND the fallback auto-stop.
			//
			// LIFECYCLE INVARIANT (C-MIC-18): this interval must be created
			// only AFTER the backend confirmed the recording started
			// (``result.success`` above) and is cleared ONLY by
			// ``stopTest`` / ``selectMicrophone`` / unmount, never by a
			// creation and froze the timer at 00:00 while the backend kept
			// recording. The backend's own auto-stop remains the primary
			// grace-period trigger below exists only as a safety net for a
			// lost push event, not as the clock source.
			if (testTimerRef.current) clearInterval(testTimerRef.current);
			const startTime = Date.now();
			const totalDurationMs =
				(result.duration || MICROPHONE_TEST_DURATION_SEC) * 1000;
			let lastWholeSecond = -1;
			const tickInterval = setInterval(() => {
				const elapsedMs = Date.now() - startTime;
				const wholeSecond = Math.min(
					MICROPHONE_TEST_DURATION_SEC,
					Math.floor(elapsedMs / 1000),
				);
				if (wholeSecond !== lastWholeSecond) {
					lastWholeSecond = wholeSecond;
					setTestElapsed(wholeSecond);
				}
				// Safety net ONLY: normally the backend's
				// ``microphone_test_complete`` event drives ``stopTest``
				// first. The +750ms grace prevents racing the backend's
				// own auto-stop/finalization.
				if (
					elapsedMs >= totalDurationMs + 750 &&
					testTimerRef.current === tickInterval
				) {
					clearInterval(tickInterval);
					testTimerRef.current = null;
					void stopTest();
				}
			}, 250);
			testTimerRef.current = tickInterval;
		} catch (err) {
			// The predecessor path surfaces the backend's
			// generic failure toast.
			const code = (err as { code?: string } | null)?.code;
			if (code === CONSENT_REQUIRED_CODE) {
				// ``usePython.call`` now preserves the structured
				// exact Settings toggle.
				const consentField = (err as { consent_field?: unknown } | null)
					?.consent_field;
				showConsentSnack(
					typeof consentField === "string"
						? consentField
						: VOICE_BIOMETRIC_CONSENT_FIELD,
				);
				return;
			}
			console.error(
				"[renderer:useMicrophoneTestSession] Failed to start microphone test:",
				err,
			);
			// Known failure classes (timeout / backend unreachable /
			// rate limit) get their curated localized message; unknown
			// ones keep the contextual "failed to start" fallback.
			showSnack(
				userFacingErrorMessage(err, t, t("microphone.startTestFailed")),
				"error",
			);
		} finally {
			startingRef.current = false;
			setTestStarting(false);
		}
	}, [call, config, showSnack, t, stopPlayback, stopTest, setLevel, setPeak]);
	// Keep the consent-retry ref pointed at the latest closure.
	startTestRef.current = startTest;

	const selectMicrophone = useCallback(
		async (micId: string | null) => {
			// Stop any active test first
			if (
				(testRunningRef.current || recordingActiveRef.current) &&
				!stoppingRef.current
			) {
				recordingActiveRef.current = false;
				try {
					await callRef.current("microphone_test_cancel");
				} catch (e) {
					/* ignore, test may have already finished, or the
                                           backend may be tearing down */
					console.warn(
						"[renderer:useMicrophoneTestSession] selectMicrophone cancel failed:",
						e,
					);
				}
				setTestRunning(false);
				setTestAudioBase64(null);
				setRawAudioBase64(null);
				setTestQuality(null);
				setTestTranscription(null);
				setTestTranscriptionUnavailable(false);
				if (testTimerRef.current) {
					clearInterval(testTimerRef.current);
					testTimerRef.current = null;
				}
			}

			setTestAudioBase64(null);
			setRawAudioBase64(null);
			setTestQuality(null);
			setTestTranscription(null);
			setTestTranscriptionUnavailable(false);
			// Invalidate the test recording cache on a
			// mic and would be misleading A/B comparison material
			// against the new mic. Mirrors the startTest invalidation.
			// Also invalidate any pending silent-expiry timer so it can
			// never clear a later test.
			expiryGenRef.current += 1;
			if (expiryTimerRef.current) {
				clearTimeout(expiryTimerRef.current);
				expiryTimerRef.current = null;
			}
			_resetMicrophoneTestCache();

			try {
				await callRef.current("set_config", { microphone: micId });
				setConfig((prev) => (prev ? { ...prev, microphone: micId } : prev));
				setLevel(0);
				setPeak(0);
				setMicMonitoring(false);
				// level-monitor effect in ``useMicrophoneLevelMonitor``
				// re-runs ``level_monitor_start`` whenever
				// ``config.microphone`` changes (its dep array includes
				// ``config?.microphone``). Calling it again here was a
				// double-start: the effect's cleanup sends
				// ``level_monitor_stop`` for the OLD mic, then re-sends
				// ``level_monitor_start`` for the NEW mic, so the explicit
				// call here produced TWO ``level_monitor_start`` IPCs per
				// mic switch.
				const label =
					micId === null
						? t("microphone.systemDefault")
						: (microphones.find((m) => (m.id ?? String(m.index)) === micId)
								?.name ?? t("microphone.microphone"));
				showSnack(t("microphone.usingMic", { name: label }), "success");
			} catch (err) {
				// Known failure classes surface their curated message;
				// unknown ones keep the contextual fallback.
				showSnack(
					userFacingErrorMessage(err, t, t("microphone.setFailed")),
					"error",
				);
			}
		},
		[
			// selectMicrophoneRef sync effect below (deps
			// [selectMicrophone]) is the documented-to-be-stable
			// consumer, and an identity churn under test mocks would
			// re-assign the shared ref on every render. The remaining
			// deps (microphones, setters, showSnack, t) are genuine
			// state deps and stay.
			microphones,
			setConfig,
			showSnack,
			t,
			setLevel,
			setPeak,
			setMicMonitoring,
			testRunningRef,
			callRef,
		],
	);

	// When the backend finishes recording, drive ``stopTest`` to fetch
	// ``useCallback``-stable, we capture it directly (no ``stopTestRef``
	// indirection). The subscription re-binds when ``testRunning`` OR
	// ``stopTest`` changes, ``stopTest`` changes are bounded by its
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

	// mic stream open after navigation. MUST NOT depend on
	// ``testRunning``: a dep-driven cleanup re-runs on every
	// which are exactly the intervals ``startTest`` created one commit
	// earlier (the frozen-00:00 timer bug). Audio-pausing on unmount is
	// owned by ``useMicrophonePlayback`` (its own cleanup effect).
	useEffect(() => {
		return () => {
			// Invalidate any pending silent-expiry timer so an unmounted
			// test can never clear a later mount's recording.
			expiryGenRef.current += 1;
			if (expiryTimerRef.current) {
				clearTimeout(expiryTimerRef.current);
				expiryTimerRef.current = null;
			}
			if (testTimerRef.current) {
				clearInterval(testTimerRef.current);
				testTimerRef.current = null;
			}
			if (recordingActiveRef.current) {
				recordingActiveRef.current = false;
				callRef
					.current("microphone_test_cancel")
					.catch((err) =>
						console.warn(
							"[renderer:useMicrophoneTestSession] microphone command failed: microphone_test_cancel:",
							err,
						),
					);
			}
		};
	}, [callRef]);

	// Keep ``selectMicrophoneRef`` pointed at the latest stable
	//``selectMicrophone`` closure (). The assignment now happens
	// via an effect with ``[selectMicrophone]`` deps instead of on
	// every render, now that ``selectMicrophone`` is
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
		testStarting,
		testElapsed,
		testAudioBase64,
		rawAudioBase64,
		testDurationMs,
		testQuality,
		testTranscription,
		testTranscriptionUnavailable,
		filtersSinceLastTest,
		startTest,
		stopTest,
		selectMicrophone,
	};
}
