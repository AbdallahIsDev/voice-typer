// Test-session state machine hook for the Microphone page.
//
//Extracted from the former ``useMicrophoneTest`` monolith ().
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
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
import { buildTestFilters } from "../lib/buildTestFilters";
import { computeAudioKey } from "../lib/computeAudioKey";
import type {
	TestAudioChunk,
	TestResultQuality,
	TestStopResult,
} from "../lib/types";

/**
 * Fixed microphone-test recording duration, in seconds. The test is
 * permanently 10 seconds — there is no user-facing configurability.
 * Single source of truth for the ``microphone_test_start`` payload and
 * the countdown fallback; the backend independently clamps to
 * [1.0, 60.0].
 */
export const MICROPHONE_TEST_DURATION_SEC = 10;

// Module-level cache for the last-test recording + quality
// verdict — mirrors the ``_cachedMicrophones`` / ``_cachedConfig``
// pattern in ``useMicrophoneData``. Persists across page navigations so
// a user who runs a mic test, navigates to the Models page to download
// a model, then returns to the Microphone page sees their previous
// test's recording + verdict WITHOUT having to re-run the test (which
// would otherwise be lost — the test audio + quality were React-state
// only, cleared on unmount). The cache is invalidated whenever:
//   • ``startTest`` runs (a new test supersedes the old one), or
//   • ``selectMicrophone`` picks a different mic (the cached recording
//     was for a DIFFERENT mic — keeping it would be misleading A/B
//     comparison material).
let _cachedTestAudioBase64: string | null = null;
let _cachedRawAudioBase64: string | null = null;
let _cachedTestQuality: TestResultQuality | null = null;
let _cachedTestDurationMs: number = 0;
let _cachedTestTranscription: string | null = null;
let _cachedTestTranscriptionUnavailable: boolean = false;

/**
 * Reset the module-level test cache. Exported for tests + for the
 * session hook's own use when invalidating on a mic switch. The cached
 * recordings are tied to the PREVIOUS mic + filter config — keeping
 * them across a mic switch would let the user "play" the wrong
 * recording against the wrong mic (mismatched A/B).
 */
export function _resetMicrophoneTestCache(): void {
	_cachedTestAudioBase64 = null;
	_cachedRawAudioBase64 = null;
	_cachedTestQuality = null;
	_cachedTestDurationMs = 0;
	_cachedTestTranscription = null;
	_cachedTestTranscriptionUnavailable = false;
}

/**
 * Binary bytes fetched per ``microphone_test_read_audio`` chunk. Each
 * response stays well under the 1 MiB IPC frame cap; a completed 10 s
 * test WAV is ~0.9 MB, so ~5 chunks per file.
 */
const AUDIO_CHUNK_BYTES = 256 * 1024;

/**
 * Single-flight registry: concurrent ``fetchTestAudioFile`` calls for the
 * SAME path share one in-flight promise instead of issuing parallel
 * duplicate ``microphone_test_read_audio`` request bursts (which would
 * double the slice count against the shared per-connection rate budget
 * for no benefit). Entries self-remove on settle.
 */
const _inFlightAudioFetches = new Map<string, Promise<string>>();

function fetchTestAudioFileDeduped(
	call: PythonCall,
	path: string,
): Promise<string> {
	const existing = _inFlightAudioFetches.get(path);
	if (existing) return existing;
	const p = fetchTestAudioFile(call, path).finally(() => {
		_inFlightAudioFetches.delete(path);
	});
	_inFlightAudioFetches.set(path, p);
	return p;
}

/**
 * Fetch a persisted mic-test WAV via the chunked file-reference IPC
 * transport and return its full base64 payload (playback keeps using
 * data URIs, so only the TRANSPORT is chunked — the assembled result
 * shape is unchanged).
 *
 * The backend persists each completed test's WAVs on disk precisely
 * because a base64 double-WAV stop payload exceeded the 1 MiB frame cap
 * and was silently dropped, leaving the 10 s recording unusable.
 */
async function fetchTestAudioFile(
	call: PythonCall,
	path: string,
): Promise<string> {
	let offset = 0;
	const parts: string[] = [];
	// Bounded loop: total/bytes_read come from the backend; the guard
	// prevents an endless loop against a buggy server.
	for (let safety = 0; safety < 1024; safety++) {
		const res = await call<TestAudioChunk>("microphone_test_read_audio", {
			path,
			offset,
			length: AUDIO_CHUNK_BYTES,
		});
		if (!res?.success) {
			throw new Error(res?.message || "audio chunk read failed");
		}
		if (res.data_b64) parts.push(res.data_b64);
		offset += res.bytes_read || 0;
		if (res.eof || offset >= (res.total_bytes || Infinity)) break;
	}
	return parts.join("");
}

/** Type of the ``t()`` i18n function — accepts a key + optional params. */
type TFunction = (key: string, params?: Record<string, string>) => string;

/** Type of the ``showSnack`` toast function (matches ``useSnackbar``). */
type ShowSnack = (
	message: string,
	type?: SnackbarType,
	options?: ShowSnackOptions,
) => void;

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
	// NOTE: the former ``onOpenPrivacySettings`` prop was REMOVED when
	// the consent-required snackbar was replaced by the unified
	// point-of-use consent dialog (ConsentGateDialog) — the dialog's
	// "Open Settings" action navigates itself via the consentGate
	// store, so the per-hook callback was dead code. See
	// consentGate.ts.
}

export interface UseMicrophoneTestSessionResult {
	testRunning: boolean;
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
	// effect below must not depend on the `call` identity — a test mock
	// handing out a fresh `call` per render would re-fire it (OOM loop
	// class). ``callRef.current`` is read at cleanup time instead.
	const callRef = useLatestRef(call);
	// ``updateConfig`` is part of the public session-hook signature
	// for parity with the prior ``useMicrophoneTest`` API but is not
	// used directly here — preset / config-change handlers live in
	// the composition hook. Reference it to satisfy exhaustive-deps
	// lint without making it a runtime dep.
	void updateConfig;

	const [testRunning, setTestRunning] = useState(false);
	const [testElapsed, setTestElapsed] = useState(0);
	// Seed the per-test React state from the module-level
	// cache so navigating away from the Microphone page and back
	// restores the last test's recording + verdict. The cache is
	// invalidated on startTest (a new test) and on selectMicrophone
	// (mic switch — the cached recording is for a different mic).
	const [testAudioBase64, setTestAudioBase64] = useState<string | null>(
		_cachedTestAudioBase64,
	);
	const [rawAudioBase64, setRawAudioBase64] = useState<string | null>(
		_cachedRawAudioBase64,
	);
	const [testDurationMs, setTestDurationMs] = useState(_cachedTestDurationMs);
	const [testQuality, setTestQuality] = useState<TestResultQuality | null>(
		_cachedTestQuality,
	);
	const [testTranscription, setTestTranscription] = useState<string | null>(
		_cachedTestTranscription,
	);
	const [testTranscriptionUnavailable, setTestTranscriptionUnavailable] =
		useState(_cachedTestTranscriptionUnavailable);
	// Tracks whether filters have changed since last test (invalidation).
	const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>("");

	const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const stoppingRef = useRef(false);
	// INTERNAL lifecycle flag owned by THIS hook (synchronous). The
	// ``testRunningRef`` prop stays the cross-hook CONTRACT mirror for the
	// level monitor, but the unmount cleanup must not depend on a prop-ref's
	// identity staying stable across renders — read our own flag instead.
	const recordingActiveRef = useRef(false);
	// Latest ``startTest`` closure, so the consent dialog's retry
	// (shown INSIDE startTest) can re-run the FULL start after the
	// user grants consent — the closure identity isn't available to
	// itself while it's being defined.
	const startTestRef = useRef<() => Promise<void>>(async () => {});
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
				// The recording itself succeeded — duration/quality verdict
				// and transcription are valid even when playback delivery
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
				// Mirror the freshly-captured test recording into
				// the module-level cache so a page navigation does NOT
				// discard it. Mirrors the ``_cachedConfig`` write-through
				// pattern in useMicrophoneData.updateConfig.
				_cachedTestAudioBase64 = audioB64;
				_cachedRawAudioBase64 = rawB64;
				_cachedTestDurationMs = result.duration_ms || 0;
				if (result.quality) {
					_cachedTestQuality = result.quality;
				}
				_cachedTestTranscription = transcriptionText;
				_cachedTestTranscriptionUnavailable = transcriptionUnavailable;
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
				// retry landed after completion). Deterministic state handling —
				// NOT a failure; toasting it trained users to ignore real errors.
				return;
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
	}, [call, config, showSnack, t, setLevel]);

	const startTest = useCallback(async () => {
		setTestAudioBase64(null);
		setRawAudioBase64(null);
		setTestDurationMs(0);
		setTestQuality(null);
		setTestTranscription(null);
		setTestTranscriptionUnavailable(false);
		// Invalidate the module-level test cache when a
		// new test starts — the cache holds the PREVIOUS test's
		// recording, which is now superseded. The setX calls above
		// update React state immediately; the cache reset keeps the
		// module-level copy in lockstep so a navigation during the
		// test doesn't surface stale data on return.
		_cachedTestAudioBase64 = null;
		_cachedRawAudioBase64 = null;
		_cachedTestQuality = null;
		_cachedTestDurationMs = 0;
		_cachedTestTranscription = null;
		_cachedTestTranscriptionUnavailable = false;
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

		// Shared point-of-use consent dialog (deduped across the
		// resolved-envelope + thrown-error paths below, and shared with
		// the level-monitor path). Opens the UNIFIED consent gate
		// (Allow → persists the consent → retries the full test start
		// via ``startTestRef``; "Open Settings" deep-links to the
		// exact toggle). The ``consentField`` from the backend
		// envelope is forwarded so the dialog + Settings target the
		// right row (defaults to ``voice_biometric_consent`` — the
		// only field the level-monitor / mic-test gates enforce — for
		// older backends whose plain ``success:false`` envelope omits
		// it).
		const showConsentSnack = (consentField: string) => {
			openConsentGate({
				consentField,
				bodyKey: consentBodyKey(consentField),
				// Retry after granting: re-run the FULL test start — the
				// first attempt was aborted at the gate before the
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
				// (from ``_respond_with_error``'s ConsentRequiredError
				// mapping) carries a structured ``code`` field and a
				// message containing "consent required" — branch on the
				// code first (robust to message rewording), falling back
				// to the substring for older backend versions that only
				// resolve a plain ``success:false`` envelope. Surface the
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
					// Forward the structured ``consent_field`` from the
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
			// ``stopTest`` / ``selectMicrophone`` / unmount — never by a
			// dep-driven effect cleanup on the ``testRunning`` transition,
			// which previously killed these intervals one commit after
			// creation and froze the timer at 00:00 while the backend kept
			// recording. The backend's own auto-stop remains the primary
			// completion signal (``microphone_test_complete`` event); the
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
			// The Electron path surfaces the backend's
			// ``client.consent_required`` envelope as a thrown Error
			// with ``code`` preserved (see usePython.call's
			// ``type:"error"`` handling). Detect it and surface the
			// consent prompt + Settings deep-link instead of the
			// generic failure toast.
			const code = (err as { code?: string } | null)?.code;
			if (code === CONSENT_REQUIRED_CODE) {
				// ``usePython.call`` now preserves the structured
				// consent fields onto the thrown Error — forward the
				// ``consent_field`` so the deep-link scrolls to the
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
		}
	}, [call, config, showSnack, t, stopPlayback, stopTest, setLevel, setPeak]);
	// Keep the consent-retry ref pointed at the latest closure.
	startTestRef.current = startTest;

	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
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
					/* ignore — test may have already finished, or the
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
			// mic switch — the cached recording was for the PREVIOUS
			// mic and would be misleading A/B comparison material
			// against the new mic. Mirrors the startTest invalidation.
			_cachedTestAudioBase64 = null;
			_cachedRawAudioBase64 = null;
			_cachedTestQuality = null;
			_cachedTestDurationMs = 0;
			_cachedTestTranscription = null;
			_cachedTestTranscriptionUnavailable = false;

			try {
				await callRef.current("set_config", { microphone: micId });
				setConfig((prev) => (prev ? { ...prev, microphone: micId } : prev));
				setLevel(0);
				setPeak(0);
				setMicMonitoring(false);
				// NOTE: no explicit ``level_monitor_start`` here — the
				// level-monitor effect in ``useMicrophoneLevelMonitor``
				// re-runs ``level_monitor_start`` whenever
				// ``config.microphone`` changes (its dep array includes
				// ``config?.microphone``). Calling it again here was a
				// double-start: the effect's cleanup sends
				// ``level_monitor_stop`` for the OLD mic, then re-sends
				// ``level_monitor_start`` for the NEW mic — so the explicit
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
			// `call` deliberately dropped: read via the callRef mirror
			// above so this callback keeps a STABLE identity — the
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

	// Unmount-only teardown ([] deps): clear the lifecycle timer and
	// cancel an in-flight test recording so the backend doesn't keep the
	// mic stream open after navigation. MUST NOT depend on
	// ``testRunning``: a dep-driven cleanup re-runs on every
	// false→true transition and its closure clears the CURRENT refs —
	// which are exactly the intervals ``startTest`` created one commit
	// earlier (the frozen-00:00 timer bug). Audio-pausing on unmount is
	// owned by ``useMicrophonePlayback`` (its own cleanup effect).
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	useEffect(() => {
		return () => {
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
	}, []);

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
