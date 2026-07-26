// Test-recording lifecycle hook for the Microphone page.
//
// Owns all test-related state (``testRunning`` / ``testCountdown`` /
// ``testElapsed`` / ``testAudioBase64`` / ``rawAudioBase64`` /
// ``testDurationMs`` / ``testQuality`` / ``level`` / ``peak`` /
// ``micMonitoring`` / ``testDurationSec`` / ``showAdvanced`` /
// ``filtersSinceLastTest`` / ``playingEnhanced`` / ``playingOriginal``)
// and the refs needed to keep interval/timeout closures + event
// subscriptions pointed at the latest handler
// (``stopTestRef`` / ``selectMicrophoneRef`` / ``testRunningRef`` /
// ``micMonitoringRef`` / ``playingRef`` / ``testTimerRef`` /
// ``elapsedTimerRef`` / ``audioRef`` / ``stoppingRef``).
//
// Wires:
// - The level-monitor lifecycle (``level_monitor_start`` on
// mount + mic change, ``level_monitor_stop`` on unmount) plus a
// ``mic_level`` push-event subscription that replaces the prior
// 10 Hz ``microphone_test_get_level`` poll. A one-shot
// ``microphone_test_get_level`` call seeds the first read so the
// UI doesn't wait up to ~33 ms for the first push frame.
// - the ``microphone_test_complete`` event subscription that drives
// ``stopTest`` when the backend finishes recording,
// - the unmount-cleanup effect that pauses any playing test audio,
// clears the countdown / elapsed intervals, and cancels an
// in-flight test recording.
//
// Exposes ``startTest`` / ``stopTest`` / ``selectMicrophone`` /
// ``playAudio`` / ``stopPlayback`` / ``handlePresetChange`` /
// ``handleConfigChange`` handlers plus the ``setTestDurationSec`` /
// ``setShowAdvanced`` setters for the page to wire to UI controls.
//
// Reads ``config`` / ``microphones`` / ``setConfig`` / ``updateConfig``
// from ``useMicrophoneData`` so mic-selection and preset/filter changes
// flow through the same optimistic-update path as direct config edits.
// Receives ``selectMicrophoneRef`` (owned by the page, shared with
// ``useMicrophoneData``) so the data hook's ``microphones_changed``
// hot-swap handler can invoke the latest ``selectMicrophone`` closure.

import {
	type Dispatch,
	type MutableRefObject,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import type { AudioPreset } from "@/components/microphone/AudioPresetSelector";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
import { buildTestFilters } from "../lib/buildTestFilters";
import { computeAudioKey } from "../lib/computeAudioKey";
import type { TestResultQuality, TestStopResult } from "../lib/types";

interface UseMicrophoneTestOptions {
	config: VoiceTyperConfig | null;
	microphones: MicrophoneDevice[];
	setConfig: Dispatch<SetStateAction<VoiceTyperConfig | null>>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
	selectMicrophoneRef: MutableRefObject<
		(micId: string | null) => Promise<void>
	>;
}

export interface UseMicrophoneTestResult {
	// State
	testRunning: boolean;
	testCountdown: number;
	testElapsed: number;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	testDurationMs: number;
	testQuality: TestResultQuality | null;
	level: number;
	peak: number;
	micMonitoring: boolean;
	testDurationSec: number;
	showAdvanced: boolean;
	filtersSinceLastTest: string;
	playingEnhanced: boolean;
	playingOriginal: boolean;
	// Handlers
	startTest: () => Promise<void>;
	stopTest: () => Promise<void>;
	selectMicrophone: (micId: string | null) => Promise<void>;
	playAudio: (base64: string, isEnhanced: boolean) => void;
	stopPlayback: () => void;
	handlePresetChange: (preset: AudioPreset) => void;
	handleConfigChange: (updates: Partial<VoiceTyperConfig>) => void;
	// Setters
	setTestDurationSec: Dispatch<SetStateAction<number>>;
	setShowAdvanced: Dispatch<SetStateAction<boolean>>;
}

export function useMicrophoneTest({
	config,
	microphones,
	setConfig,
	updateConfig,
	selectMicrophoneRef,
}: UseMicrophoneTestOptions): UseMicrophoneTestResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	const [testRunning, setTestRunning] = useState(false);
	const [testCountdown, setTestCountdown] = useState(0);
	const [testElapsed, setTestElapsed] = useState(0);
	const [testAudioBase64, setTestAudioBase64] = useState<string | null>(null);
	const [rawAudioBase64, setRawAudioBase64] = useState<string | null>(null);
	const [testDurationMs, setTestDurationMs] = useState(0);
	const [testQuality, setTestQuality] = useState<TestResultQuality | null>(
		null,
	);
	const [level, setLevel] = useState(0);
	const [peak, setPeak] = useState(0);
	// initialize micMonitoring to ``true`` so the level
	// polling loop in the mount effect actually fires its first
	// ``microphone_test_get_level`` call. Previously this started at
	// ``false``, and since the only thing that flips it to ``true`` is
	// the polling loop seeing ``active: true`` in the response — which
	// never happened because the loop never ran — the page deadlocked
	// with a frozen "Monitoring…" indicator and zero level bar. The
	// mount effect calls ``level_monitor_start`` unconditionally, so
	// assuming monitoring is active until the backend tells us
	// otherwise is correct.
	const [micMonitoring, setMicMonitoring] = useState(true);
	// Fix 15: user-configurable test recording duration (3–30s). The
	// prior implementation hard-coded ``duration: 10`` in the
	// ``microphone_test_start`` call, which was invisible to the user
	// and not adjustable for slow readers / different test phrases.
	const [testDurationSec, setTestDurationSec] = useState(10);

	// ADR 0007: Audio preset + filter state lives in ``config`` directly.
	// No local duplicate — the AudioPresetSelector reads from / writes
	// to ``config`` via updateConfig().
	const [showAdvanced, setShowAdvanced] = useState(false);

	// Tracks whether filters have changed since last test (invalidation)
	const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>("");
	const [playingEnhanced, setPlayingEnhanced] = useState(false);
	const [playingOriginal, setPlayingOriginal] = useState(false);

	// ``levelIntervalRef`` removed — the 10 Hz polling loop is
	// replaced by the ``mic_level`` push event subscription. The other
	// refs (``testTimerRef`` / ``elapsedTimerRef``) remain because the
	// test countdown + elapsed-display still use ``setInterval``.
	const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const audioRef = useRef<HTMLAudioElement | null>(null);
	const playingRef = useRef(false);
	const stopTestRef = useRef<() => Promise<void>>(async () => {});
	const stoppingRef = useRef(false);

	// CR-57: gate the 100ms polling on visibility + active state.
	const testRunningRef = useRef(false);
	const micMonitoringRef = useRef(false);
	useEffect(() => {
		testRunningRef.current = testRunning;
	}, [testRunning]);
	useEffect(() => {
		micMonitoringRef.current = micMonitoring;
	}, [micMonitoring]);

	// level-monitor lifecycle. Started on mount + whenever the
	// selected microphone changes; torn down (and ``level_monitor_stop``
	// sent) on cleanup. Previously this hook polled
	// ``microphone_test_get_level`` via ``setInterval(100)`` at 10 Hz,
	// costing 10–40 ms/sec of CPU across renderer+host+sidecar for a
	// 3-key dict. The backend now publishes a coalesced ``mic_level``
	// push event (≤30 Hz) via the same bounded-queue pattern as
	// ``bubble_level``; we subscribe to it via ``usePythonEvent`` and
	// keep only a ONE-SHOT poll as a first-read fallback (so the UI
	// doesn't freeze for ~33 ms waiting for the first push after
	// ``level_monitor_start``).
	useEffect(() => {
		const micId = config?.microphone ?? null;
		call<{ success: boolean }>("level_monitor_start", { mic_id: micId }).catch(
			(err) =>
				console.warn(
					"[IPC] microphone command failed: level_monitor_start:",
					err,
				),
		);

		// one-shot fallback poll. The backend's ``mic_level`` push
		// is coalesced at ≤30 Hz, so the first frame may take up to ~33 ms
		// to arrive after ``level_monitor_start``. We issue a single
		// ``microphone_test_get_level`` call to seed the UI immediately;
		// subsequent updates come from the push event subscription below.
		// The fallback is a no-op if the push event arrives first (the
		// setState calls are idempotent — last write wins).
		void (async () => {
			if (
				typeof document !== "undefined" &&
				document.visibilityState !== "visible"
			)
				return;
			if (playingRef.current) return;
			try {
				const levelData = await call<{
					level: number;
					peak: number;
					active: boolean;
				}>("microphone_test_get_level");
				if (levelData && typeof levelData.level === "number") {
					setLevel(levelData.level);
				}
				if (levelData && typeof levelData.peak === "number") {
					setPeak(levelData.peak);
				}
				if (levelData && typeof levelData.active === "boolean") {
					setMicMonitoring(levelData.active);
				}
			} catch (e) {
				// Non-fatal — the push event subscription will still
				// deliver updates once the backend starts publishing.
				console.warn("[useMicrophoneTest] one-shot level poll failed:", e);
			}
		})();

		const handleVisibility = () => {
			// no-op — kept for parity with the previous implementation.
			// The push event subscription below self-gates on
			// ``testRunning || micMonitoring`` + ``!playingRef`` via the
			// handler closure (which reads the refs at event time).
		};
		if (typeof document !== "undefined") {
			document.addEventListener("visibilitychange", handleVisibility);
		}

		return () => {
			if (typeof document !== "undefined") {
				document.removeEventListener("visibilitychange", handleVisibility);
			}
			call("level_monitor_stop").catch((err) =>
				console.warn(
					"[IPC] microphone command failed: level_monitor_stop:",
					err,
				),
			);
		};
	}, [call, config?.microphone]);

	// subscribe to the backend's ``mic_level`` push event
	// (published by ``level_monitor._process_level_chunk`` via the same
	// bounded-queue + worker pattern as ``bubble_level``). Replaces the
	// 10 Hz ``setInterval(100)`` poll. The handler self-gates on the
	// same conditions as the previous poll (visibility + active state +
	// not playing) so we don't surface stale levels while the tab is
	// hidden, monitoring is paused, or the user is listening to a test
	// playback.
	usePythonEvent(
		"mic_level",
		useCallback((data?: Record<string, unknown>): (() => void) | undefined => {
			if (
				typeof document !== "undefined" &&
				document.visibilityState !== "visible"
			)
				return undefined;
			if (!testRunningRef.current && !micMonitoringRef.current)
				return undefined;
			if (playingRef.current) return undefined;
			const levelData = data as
				| { level?: unknown; peak?: unknown; active?: unknown }
				| undefined;
			if (!levelData) return undefined;
			if (typeof levelData.level === "number") {
				setLevel(levelData.level);
			}
			if (typeof levelData.peak === "number") {
				setPeak(levelData.peak);
			}
			if (typeof levelData.active === "boolean") {
				setMicMonitoring(levelData.active);
			}
			return undefined;
		}, []),
	);

	// When the backend finishes recording, drive ``stopTest`` to fetch
	// the result + reset the test-running UI. ``stopTestRef`` is kept
	// up-to-date on every render so the subscription (which only
	// re-subscribes when ``testRunning`` changes) always invokes the
	// latest closure.
	usePythonEvent(
		"microphone_test_complete",
		useCallback(
			(_data: unknown): (() => void) | undefined => {
				if (testRunning && !stoppingRef.current) {
					stopTestRef.current();
				}
				return undefined;
			},
			[testRunning],
		),
	);

	// Unmount cleanup: clear intervals, pause any playing test audio,
	// and cancel an in-flight test recording so the backend doesn't
	// keep the mic stream open after the user navigates away.
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
			// pause any playing test audio to prevent
			// background playback after navigation. Also clears the audioRef
			// so onended/onerror don't fire setState on an unmounted component.
			if (audioRef.current) {
				try {
					audioRef.current.pause();
				} catch (e) {
					/* noop — audio element may already be in a
					   closed/stopped state */
					console.warn("[useMicrophoneTest] cleanup pause failed:", e);
				}
				audioRef.current = null;
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

	const selectMicrophone = async (micId: string | null) => {
		// Stop any active test first
		if (testRunning && !stoppingRef.current) {
			try {
				await call("microphone_test_cancel");
			} catch (e) {
				/* ignore — test may have already finished, or the
				   backend may be tearing down */
				console.warn("[useMicrophoneTest] selectMicrophone cancel failed:", e);
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
	};

	const handlePresetChange = useCallback(
		(preset: AudioPreset) => {
			// ADR 0007: just set audio_preset; the backend
			// applies the preset → filter mapping from
			// voice_typer/server/audio_presets.py (single
			// source of truth).
			updateConfig({ audio_preset: preset });
		},
		[updateConfig],
	);

	const handleConfigChange = useCallback(
		(updates: Partial<VoiceTyperConfig>) => {
			updateConfig(updates);
		},
		[updateConfig],
	);

	const startTest = async () => {
		setTestAudioBase64(null);
		setRawAudioBase64(null);
		setTestDurationMs(0);
		setTestQuality(null);
		setLevel(0);
		setPeak(0);
		setPlayingEnhanced(false);
		setPlayingOriginal(false);
		setTestElapsed(0);

		const micId = config?.microphone ?? null;

		// Record the current filter state for invalidation tracking
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
					stopTestRef.current();
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
	};

	const stopTest = async () => {
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
	};

	// Keep ``stopTestRef`` / ``selectMicrophoneRef`` pointed at the
	// latest closures so the ``microphone_test_complete`` subscription
	// (above) and the ``microphones_changed`` subscription (in
	// ``useMicrophoneData``) can invoke them without re-subscribing on
	// every render. Mirrors the  ref-to-latest pattern.
	stopTestRef.current = stopTest;
	selectMicrophoneRef.current = selectMicrophone;

	const playAudio = (base64: string, isEnhanced: boolean) => {
		if (!base64) return;
		if (audioRef.current) {
			audioRef.current.pause();
			audioRef.current = null;
		}

		if (isEnhanced) {
			setPlayingEnhanced(true);
			setPlayingOriginal(false);
		} else {
			setPlayingEnhanced(false);
			setPlayingOriginal(true);
		}
		playingRef.current = true;

		try {
			const audioDataUri = `data:audio/wav;base64,${base64}`;
			const audio = new Audio(audioDataUri);
			audioRef.current = audio;

			audio.onended = () => {
				setPlayingEnhanced(false);
				setPlayingOriginal(false);
				playingRef.current = false;
				audioRef.current = null;
			};

			audio.onerror = () => {
				setPlayingEnhanced(false);
				setPlayingOriginal(false);
				playingRef.current = false;
				audioRef.current = null;
				showSnack(t("microphone.playbackFailed"), "error");
			};

			audio.play().catch(() => {
				setPlayingEnhanced(false);
				setPlayingOriginal(false);
				playingRef.current = false;
				audioRef.current = null;
				showSnack(t("microphone.playbackRetryFailed"), "error");
			});
		} catch {
			setPlayingEnhanced(false);
			setPlayingOriginal(false);
			playingRef.current = false;
			showSnack(t("microphone.startPlaybackFailed"), "error");
		}
	};

	const stopPlayback = () => {
		if (audioRef.current) {
			audioRef.current.pause();
			audioRef.current = null;
		}
		setPlayingEnhanced(false);
		setPlayingOriginal(false);
		playingRef.current = false;
	};

	return {
		// State
		testRunning,
		testCountdown,
		testElapsed,
		testAudioBase64,
		rawAudioBase64,
		testDurationMs,
		testQuality,
		level,
		peak,
		micMonitoring,
		testDurationSec,
		showAdvanced,
		filtersSinceLastTest,
		playingEnhanced,
		playingOriginal,
		// Handlers
		startTest,
		stopTest,
		selectMicrophone,
		playAudio,
		stopPlayback,
		handlePresetChange,
		handleConfigChange,
		// Setters
		setTestDurationSec,
		setShowAdvanced,
	};
}
