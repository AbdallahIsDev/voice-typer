// Test-recording lifecycle hook for the Microphone page (DR-11 split).
//
// Formerly a 625-LOC monolith; now a thin composition root over three
// focused hooks (``useMicrophoneLevelMonitor`` /
// ``useMicrophoneTestSession`` / ``useMicrophonePlayback``) plus the
// trivial UI-only state (testDurationSec / showAdvanced) +
// ``handlePresetChange`` / ``handleConfigChange`` wrappers + the
// cross-hook ``testRunningRef`` (synced by the session hook, read by
// the level monitor). Public return shape unchanged.
//
// DR-9 (1-C Finding 8): the internal ``stopTestRef`` /
// ``selectMicrophoneRef`` indirection is removed now that all five
// callbacks are ``useCallback``-stable in their respective sub-hooks.

import {
	type Dispatch,
	type MutableRefObject,
	type SetStateAction,
	useCallback,
	useRef,
	useState,
} from "react";
import type { AudioPreset } from "@/components/microphone/AudioPresetSelector";
import { usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
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

	// Fix 15: user-configurable test recording duration (3–30s).
	const [testDurationSec, setTestDurationSec] = useState(10);
	// ADR 0007: Audio preset + filter state lives in ``config`` directly.
	const [showAdvanced, setShowAdvanced] = useState(false);

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
	const levelMonitor = useMicrophoneLevelMonitor({
		config,
		playingRef: playback.playingRef,
		testRunningRef,
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
		micMonitoring: levelMonitor.micMonitoring,
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
