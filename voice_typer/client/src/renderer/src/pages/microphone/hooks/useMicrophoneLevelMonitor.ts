// Level/peak monitoring lifecycle hook for the Microphone page.
//
//Extracted from the former ``useMicrophoneTest`` monolith ().
// Owns the ``level`` / ``peak`` / ``micMonitoring`` state plus the
// ``level_monitor_start`` / ``level_monitor_stop`` IPC lifecycle, the
// ``mic_level`` push-event subscription (replaces the prior 10 Hz
// ``microphone_test_get_level`` poll), and the one-shot fallback poll
// that seeds the first read so the UI doesn't wait up to ~33 ms for the
// first push frame after ``level_monitor_start``.
//
// The push handler self-gates on the same conditions as the previous
// poll (visibility + active state + not playing) so we don't surface
// stale levels while the tab is hidden, monitoring is paused, or the
// user is listening to a test playback. The ``playingRef`` (owned by
// ``useMicrophonePlayback``) and ``testRunningRef`` (owned by
// ``useMicrophoneTestSession``) are passed in so the handler reads the
// latest values at event-fire time without rebinding.
//
// The setters (``setLevel`` / ``setPeak`` / ``setMicMonitoring``) are
// exposed so ``useMicrophoneTestSession`` can reset the meter on test
// start / stop / mic-selection. The composition hook does NOT re-export
// them — the public ``useMicrophoneTest`` API is unchanged.

import {
	type Dispatch,
	type MutableRefObject,
	type SetStateAction,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import type { VoiceTyperConfig } from "@/types/config";

interface UseMicrophoneLevelMonitorOptions {
	/** Current voice-typer config (read for ``config.microphone``). */
	config: VoiceTyperConfig | null;
	/**
	 * Ref-to-latest "is audio playing" flag, owned by
	 * ``useMicrophonePlayback``. Read at event-fire time so the
	 * push handler suppresses level updates during playback without
	 * rebinding on every render.
	 */
	playingRef: MutableRefObject<boolean>;
	/**
	 * Ref-to-latest "is test running" flag, owned by
	 * ``useMicrophoneTestSession``. Read at event-fire time so the
	 * push handler respects the ``testRunning || micMonitoring``
	 * gate without rebinding.
	 */
	testRunningRef: MutableRefObject<boolean>;
}

export interface UseMicrophoneLevelMonitorResult {
	level: number;
	peak: number;
	micMonitoring: boolean;
	/** Exposed so the session hook can reset the meter on test start/stop. */
	setLevel: Dispatch<SetStateAction<number>>;
	/** Exposed so the session hook can reset the meter on test start/stop. */
	setPeak: Dispatch<SetStateAction<number>>;
	/** Exposed so the session hook can mark monitoring inactive on mic change. */
	setMicMonitoring: Dispatch<SetStateAction<boolean>>;
}

export function useMicrophoneLevelMonitor({
	config,
	playingRef,
	testRunningRef,
}: UseMicrophoneLevelMonitorOptions): UseMicrophoneLevelMonitorResult {
	const { call } = usePython();

	const [level, setLevel] = useState(0);
	const [peak, setPeak] = useState(0);
	// Initialize micMonitoring to ``true`` so the level polling loop
	// in the mount effect actually fires its first
	// ``microphone_test_get_level`` call. Previously this started at
	// ``false``, and since the only thing that flips it to ``true`` is
	// the polling loop seeing ``active: true`` in the response — which
	// never happened because the loop never ran — the page deadlocked
	// with a frozen "Monitoring…" indicator and zero level bar. The
	// mount effect calls ``level_monitor_start`` unconditionally, so
	// assuming monitoring is active until the backend tells us
	// otherwise is correct.
	const [micMonitoring, setMicMonitoring] = useState(true);

	//gate the push handler on visibility + active state.
	const micMonitoringRef = useRef(false);
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
				console.warn(
					"[useMicrophoneLevelMonitor] one-shot level poll failed:",
					e,
				);
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
	}, [call, config?.microphone, playingRef]);

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
		useCallback(
			(data?: Record<string, unknown>): (() => void) | undefined => {
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
			},
			[playingRef, testRunningRef],
		),
	);

	return {
		level,
		peak,
		micMonitoring,
		setLevel,
		setPeak,
		setMicMonitoring,
	};
}
