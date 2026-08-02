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
//
// ──  ref+rAF pattern (mirrors ``bubble/useAudioLevels.ts``) ──
//
// Previously, the ``mic_level`` push handler called ``setLevel`` +
// ``setPeak`` on every event (≤30 Hz). Each call triggered a re-render
// of the entire Microphone page subtree (the parent ``Microphone.tsx``
// consumes ``level`` / ``peak`` directly and passes them down through
// ``ActiveMicrophoneCard`` → ``LevelBarContainer`` → ``LevelBar`` /
// ``LiveQualityFeedback``). Even though the heavy siblings
// (``MemoizedTestReviewPanel`` / ``MemoizedAudioPresetSelector``) are
// wrapped in ``React.memo``, the parent still paid the reconciliation
// cost 30 times per second.
//
// Fix: mirror ``level`` / ``peak`` into refs (``levelRef`` /
// ``peakRef``) on every ``mic_level`` event WITHOUT calling
// ``setLevel`` / ``setPeak``. A dedicated rAF loop (owned by this hook,
// gated on visibility + active state + not-playing) reads the refs and
// imperatively writes the latest level to the ``LevelBar``'s fill div
// (``[role="progressbar"] > div``) inside the consumer-attached
// ``meterRef`` wrapper. This mirrors the bubble's ``useAudioLevels``
// ref+rAF pattern at ``useAudioLevels.ts:210-218`` — the high-frequency
// path is pure ref mutation + direct-DOM write, bypassing React's
// re-render cycle entirely.
//
// ``setLevel`` / ``setPeak`` remain as React state setters so
// ``useMicrophoneTestSession`` can reset the meter to 0 on test
// start / stop / mic-change (rare, sub-Hz events). The ``level`` /
// ``peak`` STATE values are only updated on those resets — they're
// intentionally stale during mic monitoring so the parent doesn't
// re-render at 30 Hz. The live visual update happens through the rAF
// loop's imperative DOM writes, which produce the same visual result
// (LevelBar width + colour update at ≤60 Hz, smoother than the prior
// 30 Hz React-driven updates).
//
// ──  dead-code ``handleVisibility`` removed ──
//
// The prior implementation registered a ``visibilitychange`` listener
// whose body was a no-op (kept "for parity with the previous
// implementation"). The push handler already self-gates on
// ``document.visibilityState`` at event-fire time, and the rAF loop
// below also self-gates — so the listener was pure dead weight (an
// add/removeEventListener pair on every mount/unmount with no effect).
// Deleted.

import {
	type Dispatch,
	type MutableRefObject,
	type RefObject,
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
	/**
	 *  Ref-to-the-meter wrapper element. The hook's rAF loop
	 * imperatively writes the latest level/peak to the ``LevelBar``'s
	 * fill div (``[role="progressbar"] > div``) inside this wrapper,
	 * bypassing React's re-render cycle. Mirrors the bubble's
	 * ``useAudioLevels`` ref+rAF pattern (``dotRefs`` consumer-attached
	 * DOM refs). The consumer (``Microphone.tsx``) attaches this ref
	 * to a ``<div>`` wrapping ``<ActiveMicrophoneCard>``.
	 */
	meterRef: RefObject<HTMLElement | null>;
}

export interface UseMicrophoneLevelMonitorResult {
	level: number;
	peak: number;
	micMonitoring: boolean;
	/**
	 *  live level ref (mutated at ≤30 Hz by ``mic_level`` events,
	 * NOT by setState). Consumers that need to read the latest value
	 * (e.g. for text labels) can access ``.current`` directly.
	 */
	levelRef: MutableRefObject<number>;
	/**  live peak ref (mutated at ≤30 Hz by ``mic_level`` events). */
	peakRef: MutableRefObject<number>;
	/** Exposed so the session hook can reset the meter on test start/stop. */
	setLevel: Dispatch<SetStateAction<number>>;
	/** Exposed so the session hook can reset the meter on test start/stop. */
	setPeak: Dispatch<SetStateAction<number>>;
	/** Exposed so the session hook can mark monitoring inactive on mic change. */
	setMicMonitoring: Dispatch<SetStateAction<boolean>>;
}

// ──  LevelBar colour ladder ────────────────────────────────────
//
// Duplicated from ``components/feedback/LevelBar.tsx``'s private
// ``getLevelColor`` so the rAF loop can compute the fill colour without
// a React re-render. Kept in sync manually — the thresholds are stable
// (they mirror the CSS variable ladder ``--destructive`` / ``--primary``
// / ``--accent`` and haven't changed since F8). If ``LevelBar``'s
// thresholds change, update this too.
function getLevelColor(lvl: number): string {
	if (lvl > 0.7) return "var(--destructive)";
	if (lvl > 0.3) return "var(--primary)";
	return "var(--accent)";
}

export function useMicrophoneLevelMonitor({
	config,
	playingRef,
	testRunningRef,
	meterRef,
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

	//  live level/peak refs. Mutated by the ``mic_level`` push
	// handler at ≤30 Hz WITHOUT calling ``setLevel`` / ``setPeak`` — the
	// rAF loop reads these refs and writes the latest values to the DOM
	// imperatively. The React state (``level`` / ``peak``) is only
	// updated on test start/stop/mic-change resets (rare, sub-Hz), so
	// the parent ``Microphone.tsx`` doesn't re-render at 30 Hz.
	const levelRef = useRef(0);
	const peakRef = useRef(0);

	//gate the push handler on visibility + active state.
	// Mirrors the ``useState(true)`` initial value above. Previously
	// this was ``useRef(false)`` — a desync: the state initialised to
	// ``true`` (so the mount effect's ``level_monitor_start`` actually
	// fired) but the ref initialised to ``false``, so the ``mic_level``
	// push handler's ``!testRunningRef.current && !micMonitoringRef.current``
	// gate would suppress the very first push frames (until the backend's
	// ``active: true`` in a push payload flipped the state and the sync
	// effect propagated it to the ref). Initialising the ref to ``true``
	// keeps it in lockstep with the state on mount.
	const micMonitoringRef = useRef(true);
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
		//
		//  the one-shot poll still calls ``setLevel`` / ``setPeak``
		// because it runs ONCE on mount — a single React re-render is
		// fine (and desirable, so the initial state isn't stale). The
		// high-frequency ``mic_level`` push handler below is the path
		// that must avoid setState, and it does.
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
					levelRef.current = levelData.level;
					setLevel(levelData.level);
				}
				if (levelData && typeof levelData.peak === "number") {
					peakRef.current = levelData.peak;
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

		return () => {
			call("level_monitor_stop").catch((err) =>
				console.warn(
					"[IPC] microphone command failed: level_monitor_stop:",
					err,
				),
			);
		};
	}, [call, config?.microphone, playingRef]);

	//  rAF loop — imperative DOM writes for the LevelBar fill.
	//
	// Mirrors the bubble's ``useAudioLevels`` rAF pattern
	// (``useAudioLevels.ts:262-317``): the loop runs continuously while
	// the meter is mounted, reads the live ``levelRef`` / ``peakRef``
	// (mutated by the ``mic_level`` push handler at ≤30 Hz), and writes
	// the latest level to the ``LevelBar``'s fill div (``[role="progressbar"]
	// > div``) inside the consumer-attached ``meterRef`` wrapper. This
	// bypasses React's re-render cycle entirely — no ``setLevel`` /
	// ``setPeak`` calls, no parent re-render, no reconciliation of the
	// ``Microphone`` page subtree.
	//
	// Gate: the loop self-gates on the same conditions as the push
	// handler (visibility + ``testRunning || micMonitoring`` + not
	// playing). When the gate is closed, the rAF callback no-ops and
	// re-schedules the next frame (the loop stays alive so it can react
	// to gate flips without a remount — same rationale as the bubble's
	// regression guard). When the tab is hidden, the browser
	// throttles rAF to ~1 Hz, so the loop naturally pauses.
	//
	// Visual parity: the prior React-driven path updated ``LevelBar``'s
	// ``width`` + ``backgroundColor`` at ≤30 Hz via inline ``style``
	// props. This rAF loop writes the same ``width`` + ``backgroundColor``
	// to the same DOM node at ≤60 Hz — strictly smoother than the prior
	// 30 Hz cadence, with the same visual result (the bar fills to the
	// latest level with the same colour ladder).
	useEffect(() => {
		let rafId: number | null = null;

		const animate = () => {
			rafId = null;
			// Gate: skip DOM writes when hidden / not monitoring / playing.
			// rAF still re-schedules so the loop can react to gate flips.
			if (
				typeof document !== "undefined" &&
				document.visibilityState !== "visible"
			) {
				rafId = requestAnimationFrame(animate);
				return;
			}
			if (!testRunningRef.current && !micMonitoringRef.current) {
				rafId = requestAnimationFrame(animate);
				return;
			}
			if (playingRef.current) {
				rafId = requestAnimationFrame(animate);
				return;
			}

			const meter = meterRef.current;
			if (meter) {
				// LevelBar's fill div — ``[role="progressbar"] > div``.
				// Selector mirrors ``LevelBar.tsx``'s render structure
				// (the ``<div role="progressbar">`` wrapper + its single
				// child ``<div>`` carrying the inline ``width`` /
				// ``backgroundColor`` style). If ``LevelBar``'s DOM
				// changes, update this selector.
				const fill = meter.querySelector<HTMLElement>(
					'[role="progressbar"] > div',
				);
				if (fill) {
					const lvl = levelRef.current;
					fill.style.width = `${Math.max(0, lvl * 100)}%`;
					fill.style.backgroundColor = getLevelColor(lvl);
				}
			}

			rafId = requestAnimationFrame(animate);
		};

		rafId = requestAnimationFrame(animate);

		return () => {
			if (rafId !== null) {
				cancelAnimationFrame(rafId);
				rafId = null;
			}
		};
	}, [meterRef, playingRef, testRunningRef]);

	// subscribe to the backend's ``mic_level`` push event
	// (published by ``level_monitor._process_level_chunk`` via the same
	// bounded-queue + worker pattern as ``bubble_level``). Replaces the
	// 10 Hz ``setInterval(100)`` poll. The handler self-gates on the
	// same conditions as the previous poll (visibility + active state +
	// not playing) so we don't surface stale levels while the tab is
	// hidden, monitoring is paused, or the user is listening to a test
	// playback.
	//
	//  the handler mirrors ``level`` / ``peak`` into refs
	// (``levelRef.current = data.level``) WITHOUT calling ``setLevel`` /
	// ``setPeak``. The rAF loop above reads the refs and imperatively
	// updates the DOM, so the visual update path is decoupled from
	// React's re-render cycle. ``setMicMonitoring`` is still called when
	// ``active`` flips (rare, sub-Hz) because the ``micMonitoring`` flag
	// drives the "Monitoring…" / "Level: X%" label toggle in
	// ``ActiveMicrophoneCard``, which DOES need a re-render to update.
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
				//  mutate refs, NOT state. The rAF loop reads these
				// refs and writes the DOM imperatively. No setState on
				// the high-frequency path → no parent re-render at 30 Hz.
				if (typeof levelData.level === "number") {
					levelRef.current = levelData.level;
				}
				if (typeof levelData.peak === "number") {
					peakRef.current = levelData.peak;
				}
				// ``active`` flips rarely (monitoring start/stop) — safe
				// to drive a React re-render here so the label toggles.
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
		levelRef,
		peakRef,
		setLevel,
		setPeak,
		setMicMonitoring,
	};
}
