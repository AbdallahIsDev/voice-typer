// Onboarding-scoped wrapper around the Microphone page's test-session +
// level-monitor hooks.
//
// The Microphone page composes `useMicrophoneLevelMonitor` +
// `useMicrophoneTestSession` + `useMicrophonePlayback` into a single
// "test recording" feature. The Onboarding wizard only needs a thin
// slice of that feature: a "Test microphone" button + a live input-
// level meter so the user can verify their selection works before
// advancing. We DON'T need playback, peak-hold, or test-result
// quality scoring — those are page-level concerns.
//
// This hook reuses both upstream hooks unchanged (read-only imports —
//we must NOT edit those files per the  task brief). We supply
// no-op stubs for the dependencies the upstream hooks require but
// the wizard doesn't have (no-op `stopPlayback`, no-op `setConfig` /
// `updateConfig` since the wizard's mic selection is committed via
// `onboarding_set_microphone` on step advance, not via `set_config`).
//
// The hook accepts the wizard's `selectedMic` (string mic id) +
// `microphones` (MicrophoneOption[]) and adapts them to the
// `MicrophoneDevice[]` shape the upstream hooks expect.

import {
	type Dispatch,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
} from "react";
import { type PythonCall, usePython } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { useMicrophoneLevelMonitor } from "@/pages/microphone/hooks/useMicrophoneLevelMonitor";
import { useMicrophoneTestSession } from "@/pages/microphone/hooks/useMicrophoneTestSession";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";
import { ONBOARDING_MIC_TEST_DURATION_SEC } from "../lib/constants";
import type { MicrophoneOption } from "../lib/types";

/** No-op setter the upstream `useMicrophoneTestSession` requires for
 * `setConfig` parity but the wizard never invokes (mic selection is
 * committed via `onboarding_set_microphone`, not `set_config`). */
const NOOP_SET_CONFIG: Dispatch<
	SetStateAction<VoiceTyperConfig | null>
> = () => {};
/** No-op `updateConfig` — same rationale as `NOOP_SET_CONFIG`. */
const NOOP_UPDATE_CONFIG = () => {};
/** No-op `stopPlayback` — the wizard doesn't render test playback UI. */
const NOOP_STOP_PLAYBACK = () => {};

/** Adapt the wizard's `MicrophoneOption` (no `index` field) to the
 * `MicrophoneDevice` shape the upstream hooks expect. We synthesise a
 * stable `index` from the array position so the upstream hooks can
 * still key devices by `mic.id ?? String(mic.index)` (their existing
 * convention). */
function toMicrophoneDevices(
	microphones: MicrophoneOption[],
): MicrophoneDevice[] {
	return microphones.map((m, i) => ({
		index: i,
		id: m.id,
		name: m.name,
		// `host_api` is required by `MicrophoneDevice` but the
		// wizard doesn't surface it (the upstream hooks don't
		// read it). Pass an empty string so the type checks.
		host_api: "",
		default: m.default,
		is_bluetooth: m.is_bluetooth,
	}));
}

export interface UseOnboardingMicTestResult {
	/** `true` while a test recording is in flight. */
	testRunning: boolean;
	/** Live input-level (0..1) for the meter. */
	level: number;
	/** Start a 3s test recording. The backend pushes `mic_level`
	 * events during the recording so the meter animates. */
	startTest: () => Promise<void>;
	/** Stop an in-flight test recording early. */
	stopTest: () => Promise<void>;
}

/**
 * Wire up `useMicrophoneLevelMonitor` + `useMicrophoneTestSession`
 * for the Onboarding wizard's Microphone step. Accepts the wizard's
 * `selectedMic` + `microphones` (re-fetched whenever the user clicks
 * "Refresh microphones" — see `useOnboardingWizard.refreshMics`) so
 * the meter + test always target the user's current selection.
 */
export function useOnboardingMicTest(
	selectedMic: string,
	microphones: MicrophoneOption[],
): UseOnboardingMicTestResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	// Build a `VoiceTyperConfig`-shaped object that the upstream hooks
	// accept. Only the `microphone` field is meaningful for the level
	// monitor + test session; the rest are omitted (the upstream hooks
	// null-check before reading other fields).
	const config: VoiceTyperConfig | null = selectedMic
		? ({ microphone: selectedMic } as VoiceTyperConfig)
		: null;

	// Refs the upstream hooks read at event-fire time (so their
	// closures don't rebind on every render).
	const testRunningRef = useRef(false);
	const playingRef = useRef(false);

	// Local state the upstream hooks drive via the setters they
	// return / require. The level monitor owns `level` / `peak` /
	// `micMonitoring`; the test session resets them on test start /
	// stop. We forward the setters from the level monitor to the test
	// session so both hooks share the same level/peak/micMonitoring
	// state.
	const { level, setLevel, setPeak, setMicMonitoring } =
		useMicrophoneLevelMonitor({
			config,
			playingRef,
			testRunningRef,
		});

	const microphonesDevices = toMicrophoneDevices(microphones);

	const { testRunning, startTest, stopTest } = useMicrophoneTestSession({
		call: call as PythonCall,
		config,
		microphones: microphonesDevices,
		setConfig: NOOP_SET_CONFIG,
		updateConfig: NOOP_UPDATE_CONFIG,
		showSnack,
		t,
		testDurationSec: ONBOARDING_MIC_TEST_DURATION_SEC,
		setLevel,
		setPeak,
		setMicMonitoring,
		stopPlayback: NOOP_STOP_PLAYBACK,
		testRunningRef,
	});

	// Mirror the upstream `testRunning` into `testRunningRef` so the
	// level monitor's `mic_level` push handler gates updates on the
	// latest value. The upstream `useMicrophoneTestSession` already
	//syncs `testRunningRef` via its own effect (), so this is
	// belt-and-braces — but doing it here keeps the wrapper
	// self-contained and robust against future upstream changes.
	useEffect(() => {
		testRunningRef.current = testRunning;
	}, [testRunning]);

	// Stop the level monitor + cancel any in-flight test when the
	// user advances to the next step (component unmount). The
	// upstream hooks already do their own unmount cleanup, so this is
	// defensive — but the upstream `useMicrophoneTestSession`'s
	// unmount effect only fires `microphone_test_cancel` if
	// `testRunning` is true at unmount time, which is the correct
	// behaviour.
	const stopTestStable = useCallback(() => stopTest(), [stopTest]);

	return {
		testRunning,
		level,
		startTest,
		stopTest: stopTestStable,
	};
}
