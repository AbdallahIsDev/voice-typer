//Test-recording lifecycle hook for the Microphone page ( split).
//
// Formerly a 625-LOC monolith; now a thin composition root over three
// focused hooks (``useMicrophoneLevelMonitor`` /
// ``useMicrophoneTestSession`` / ``useMicrophonePlayback``) plus the
// trivial UI-only state (testDurationSec / showAdvanced) +
// ``handlePresetChange`` / ``handleConfigChange`` wrappers + the
// cross-hook ``testRunningRef`` (synced by the session hook, read by
// the level monitor). Public return shape unchanged.
//
//(1-C Finding 8): the internal ``stopTestRef`` /
// ``selectMicrophoneRef`` indirection is removed now that all five
// callbacks are ``useCallback``-stable in their respective sub-hooks.

import {
	type Dispatch,
	type MutableRefObject,
	type RefObject,
	type SetStateAction,
	useCallback,
	useRef,
	useState,
} from "react";
import type { AudioPreset } from "@/components/microphone/AudioPresetSelector";
import { useFilterState } from "@/hooks/useFilterState";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { VOICE_BIOMETRIC_CONSENT_FIELD } from "@/lib/consent";
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";

import type { TestResultQuality } from "../lib/types";
import { useMicrophoneLevelMonitor } from "./useMicrophoneLevelMonitor";
import { useMicrophonePlayback } from "./useMicrophonePlayback";
import { useMicrophoneTestSession } from "./useMicrophoneTestSession";

interface UseMicrophoneTestOptions {
	config: VoiceTyperConfig | null;
	microphones: MicrophoneDevice[];
	setConfig: Dispatch<SetStateAction<VoiceTyperConfig | null>>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
	selectMicrophoneRef: MutableRefObject<
		(micId: string | null) => Promise<void>
	>;
	/**
	 * consumer-attached ref to the meter wrapper element. The
	 * level monitor's rAF loop imperatively writes the latest level to
	 * the ``LevelBar``'s fill div inside this wrapper, bypassing React's
	 * re-render cycle. ``Microphone.tsx`` creates this ref and attaches
	 * it to a ``<div>`` wrapping ``<ActiveMicrophoneCard>``.
	 */
	meterRef: RefObject<HTMLElement | null>;
	/**
	 * Force-pause the level monitor while the active microphone is
	 * lost (``device_lost``). Passed straight through to
	 * ``useMicrophoneLevelMonitor`` — see its ``paused`` option.
	 */
	levelMonitorPaused?: boolean;
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
	/** Live level ref (mutated at ≤30 Hz by ``mic_level`` events). */
	levelRef: MutableRefObject<number>;
	/** Live peak ref (mutated at ≤30 Hz by ``mic_level`` events). */
	peakRef: MutableRefObject<number>;
	micMonitoring: boolean;
	/**
	 *  True when level monitoring is blocked by the voice-biometric
	 * consent toggle (the page renders the consent banner instead of a
	 * silently dead meter).
	 */
	consentBlocked: boolean;
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
	meterRef,
	levelMonitorPaused = false,
}: UseMicrophoneTestOptions): UseMicrophoneTestResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	// Fix 15: user-configurable test recording duration (3–30s).
	const [testDurationSec, setTestDurationSec] = useState(10);
	// ADR 0007: Audio preset + filter state lives in ``config`` directly.
	// (XA-5-4): persist the "Show advanced filters" expand toggle across
	// page navigation so a user who expanded the advanced panel to tweak
	// a noise gate threshold doesn't have to re-expand it after a
	// navigation. The toggle is purely a UI affordance (no behaviour
	// change), so persisting it is safe.
	const [showAdvanced, setShowAdvanced] = useFilterState<boolean>(
		"microphone",
		"showAdvanced",
		false,
	);

	// Cross-hook ``testRunningRef`` — owned here so the level monitor
	// (reads it) and the session hook (syncs it via internal effect)
	// can both receive it without a circular declaration dependency.
	const testRunningRef = useRef(false);

	// Audio playback — created first so its ``playingRef`` is available
	// to the level monitor below.
	const playback = useMicrophonePlayback();

	// Level/peak monitoring — created before the session hook so the
	// session can receive the level monitor's stable ``useState``
	// setters. The level monitor reads ``playingRef`` (playback) and
	// ``testRunningRef`` (composition-owned, synced by the session).
	// The level monitor also receives ``meterRef`` so its rAF
	// loop can imperatively update the ``LevelBar``'s fill div without
	// triggering parent re-renders at 30 Hz.
	// Level-monitor consent refusal (a race: consent revoked between the
	// renderer gate and the IPC) opens the unified point-of-use consent
	// dialog — Allow → persists the consent → restarts the level
	// monitor; "Open Settings" deep-links to the exact toggle (the
	// dialog's built-in secondary action, replacing the old snackbar).
	const handleLevelMonitorConsentRequired = useCallback(
		(consentField?: string) => {
			const field = consentField ?? VOICE_BIOMETRIC_CONSENT_FIELD;
			openConsentGate({
				consentField: field,
				bodyKey: consentBodyKey(field),
				// Retry after granting: restart the level monitor (the
				// consent race is resolved once the flag is persisted).
				onAllow: () =>
					call("level_monitor_start", {
						mic_id: config?.microphone ?? null,
					}),
			});
		},
		[call, config?.microphone],
	);

	const levelMonitor = useMicrophoneLevelMonitor({
		config,
		playingRef: playback.playingRef,
		testRunningRef,
		meterRef,
		paused: levelMonitorPaused,
		onConsentRequired: handleLevelMonitorConsentRequired,
	});

	// Test-session state machine — receives the level monitor's
	// setters (meter resets), the playback hook's ``stopPlayback``
	// (pause any playing audio when a new test starts), and the
	// composition-owned ``testRunningRef`` (synced via an internal
	// effect).
	const session = useMicrophoneTestSession({
		call,
		config,
		microphones,
		setConfig,
		updateConfig,
		showSnack,
		t,
		testDurationSec,
		setLevel: levelMonitor.setLevel,
		setPeak: levelMonitor.setPeak,
		setMicMonitoring: levelMonitor.setMicMonitoring,
		stopPlayback: playback.stopPlayback,
		testRunningRef,
		selectMicrophoneRef,
	});

	// Trivial UI handlers — pure pass-throughs to ``updateConfig``.
	const handlePresetChange = useCallback(
		(preset: AudioPreset) => {
			// ADR 0007: backend applies preset → filter mapping.
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

	return {
		level: levelMonitor.level,
		peak: levelMonitor.peak,
		// Expose live refs so consumers that need the latest
		// value (e.g. for text labels rendered outside the rAF-written
		// DOM) can read ``.current`` without waiting for a re-render.
		levelRef: levelMonitor.levelRef,
		peakRef: levelMonitor.peakRef,
		micMonitoring: levelMonitor.micMonitoring,
		consentBlocked: levelMonitor.consentBlocked,
		testRunning: session.testRunning,
		testCountdown: session.testCountdown,
		testElapsed: session.testElapsed,
		testAudioBase64: session.testAudioBase64,
		rawAudioBase64: session.rawAudioBase64,
		testDurationMs: session.testDurationMs,
		testQuality: session.testQuality,
		filtersSinceLastTest: session.filtersSinceLastTest,
		playingEnhanced: playback.playingEnhanced,
		playingOriginal: playback.playingOriginal,
		testDurationSec,
		showAdvanced,
		startTest: session.startTest,
		stopTest: session.stopTest,
		selectMicrophone: session.selectMicrophone,
		playAudio: playback.playAudio,
		stopPlayback: playback.stopPlayback,
		handlePresetChange,
		handleConfigChange,
		setTestDurationSec,
		setShowAdvanced,
	};
}
