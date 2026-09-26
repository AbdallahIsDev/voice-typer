// for text/aria consumers. C-BG-1: no monitor while hidden. See

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
import { usePython, usePythonEvent } from "@/hooks/usePython";
import {
	CONSENT_REQUIRED_CODE,
	VOICE_BIOMETRIC_CONSENT_FIELD,
} from "@/lib/consent";
import type { LausuConfig } from "@/types/config";

const START_RETRY_DELAYS_MS: readonly number[] = [1000, 2000, 4000];

let monitorStartIssuedSeq = 0;
let monitorStopClaimedSeq = 0;

interface UseMicrophoneLevelMonitorOptions {
	/**
	 * Current lausu config (read for ``config.microphone``).
	 */
	config: LausuConfig | null;
	/**
	 * Ref-to-latest "is audio playing" flag, owned by
	 * ``useMicrophonePlayback``. Read at event-fire time so the
	 */
	playingRef: RefObject<boolean>;
	/**
	 * Ref-to-latest "is test running" flag, owned by
	 * ``useMicrophoneTestSession``. Read at event-fire time so the
	 */
	testRunningRef: RefObject<boolean>;
	/**
	 * Ref-to-the-meter wrapper element. The hook's rAF loop
	 * imperatively writes the latest level/peak to the ``LevelBar``'s
	 */
	meterRef: RefObject<HTMLElement | null>;
	/**
	 * When true, the level monitor is force-paused: the mount effect
	 * skips ``level_monitor_start`` and sends ``level_monitor_stop``
	 */
	paused?: boolean;
	/**
	 * Optional callback invoked when ``level_monitor_start`` is refused
	 * with the backend's ``client.consent_required`` envelope (a race:
	 */
	onConsentRequired?: (consentField?: string) => void;
}

export interface UseMicrophoneLevelMonitorResult {
	level: number;
	peak: number;
	micMonitoring: boolean;
	/**
	 * live level ref (mutated at ≤30 Hz by ``mic_level`` events,
	 * NOT by setState). Consumers that need to read the latest value
	 */
	levelRef: RefObject<number>;
	/**
	 * live peak ref (mutated at ≤30 Hz by ``mic_level`` events).
	 */
	peakRef: RefObject<number>;
	/**
	 * Exposed so the session hook can reset the meter on test start/stop.
	 */
	setLevel: Dispatch<SetStateAction<number>>;
	/**
	 * Exposed so the session hook can reset the meter on test start/stop.
	 */
	setPeak: Dispatch<SetStateAction<number>>;
	/**
	 * Exposed so the session hook can mark monitoring inactive on mic change.
	 */
	setMicMonitoring: Dispatch<SetStateAction<boolean>>;
}

// not an inline style), so the rAF loop below must NOT write

export function useMicrophoneLevelMonitor({
	config,
	playingRef,
	testRunningRef,
	meterRef,
	paused = false,
	onConsentRequired,
}: UseMicrophoneLevelMonitorOptions): UseMicrophoneLevelMonitorResult {
	const { call } = usePython();

	const callRef = useLatestRef(call);

	const [level, setLevel] = useState(0);
	const [peak, setPeak] = useState(0);
	const [micMonitoring, setMicMonitoring] = useState(true);

	const levelRef = useRef(0);
	const peakRef = useRef(0);

	const micMonitoringRef = useRef(true);
	useEffect(() => {
		micMonitoringRef.current = micMonitoring;
	}, [micMonitoring]);

	useEffect(() => {
		// covers any ≤ N start still queued ahead of it on the wire.
		const sendStopClaiming = (upToSeq: number): void => {
			if (monitorStopClaimedSeq >= upToSeq) return;
			monitorStopClaimedSeq = upToSeq;
			callRef
				.current("level_monitor_stop")
				.catch((err) =>
					console.warn(
						"[renderer:useMicrophoneLevelMonitor] microphone command failed: level_monitor_stop:",
						err,
					),
				);
		};
		if (paused) {
			setMicMonitoring(false);
			sendStopClaiming(monitorStartIssuedSeq);
			return;
		}
		if (!config?.voice_biometric_consent) return;
		const micId = config?.microphone ?? null;

		// Privacy gate: do not start monitoring while the document is
		const isHiddenAtStart =
			typeof document !== "undefined" && document.visibilityState !== "visible";

		let cancelled = false;
		let retryTimer: ReturnType<typeof setTimeout> | null = null;
		let attempt = 0;
		let startedHere = false;
		let issuedStartSeq = 0;
		let deferredVisibleCleanup: (() => void) | null = null;

		const startMonitor = (): void => {
			issuedStartSeq = ++monitorStartIssuedSeq;
			callRef
				.current<{ success: boolean }>("level_monitor_start", {
					mic_id: micId,
				})
				.then(() => {
					// flight sets `cancelled`, so the cleanup must NOT stop
					if (!cancelled) {
						startedHere = true;
						return;
					}
					if (monitorStartIssuedSeq === issuedStartSeq) {
						sendStopClaiming(issuedStartSeq);
					}
				})
				.catch((err) => {
					const code = (err as { code?: string } | null)?.code;
					if (code === CONSENT_REQUIRED_CODE && onConsentRequired) {
						const field = (err as { consent_field?: unknown } | null)
							?.consent_field;
						onConsentRequired(
							typeof field === "string" ? field : VOICE_BIOMETRIC_CONSENT_FIELD,
						);
						return;
					}
					console.warn(
						"[renderer:useMicrophoneLevelMonitor] microphone command failed: level_monitor_start:",
						err,
					);
					if (!cancelled && attempt < START_RETRY_DELAYS_MS.length) {
						retryTimer = setTimeout(() => {
							retryTimer = null;
							if (!cancelled) {
								attempt += 1;
								startMonitor();
							}
						}, START_RETRY_DELAYS_MS[attempt]);
					}
				});
		};
		// ``mic_level`` push handler below is the path that must avoid
		const runOneShotLevelPoll = (): void => {
			void (async () => {
				if (
					typeof document !== "undefined" &&
					document.visibilityState !== "visible"
				)
					return;
				if (playingRef.current) return;
				try {
					const levelData = await callRef.current<{
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
					console.warn(
						"[renderer:useMicrophoneLevelMonitor] one-shot level poll failed:",
						e,
					);
				}
			})();
		};
		// NOTE: this branch must NOT early-return its own listener-only
		// cleanup, the deferred start must fall through to the shared
		if (isHiddenAtStart) {
			setMicMonitoring(false);
			const onVisible = () => {
				if (
					typeof document !== "undefined" &&
					document.visibilityState === "visible" &&
					!cancelled
				) {
					if (deferredVisibleCleanup) {
						document.removeEventListener("visibilitychange", onVisible);
						deferredVisibleCleanup = null;
					}
					startMonitor();
					runOneShotLevelPoll();
				}
			};
			document.addEventListener("visibilitychange", onVisible);
			deferredVisibleCleanup = () =>
				document.removeEventListener("visibilitychange", onVisible);
		} else {
			startMonitor();
			runOneShotLevelPoll();
		}

		return () => {
			cancelled = true;
			if (retryTimer !== null) {
				clearTimeout(retryTimer);
				retryTimer = null;
			}
			if (deferredVisibleCleanup) {
				deferredVisibleCleanup();
				deferredVisibleCleanup = null;
			}
			if (startedHere) {
				sendStopClaiming(issuedStartSeq);
			}
		};
	}, [
		config?.microphone,
		config?.voice_biometric_consent,
		paused,
		playingRef,
		onConsentRequired,
		callRef,
	]);

	const lastLevelEventAtRef = useRef(0);
	const frameRef = useRef<number | null>(null);
	const wakeRef = useRef<(() => void) | null>(null);
	const lastStateSyncAtRef = useRef(0);
	const IDLE_TIMEOUT_MS = 500;
	const LEVEL_STATE_SYNC_INTERVAL_MS = 120;

	useEffect(() => {
		lastLevelEventAtRef.current = performance.now();
		lastStateSyncAtRef.current = 0;

		const animate = () => {
			frameRef.current = null;
			// Do NOT reschedule, the next ``mic_level`` event (which
			if (
				typeof document !== "undefined" &&
				document.visibilityState !== "visible"
			) {
				return;
			}
			if (!testRunningRef.current && !micMonitoringRef.current) {
				return;
			}
			if (playingRef.current) return;

			const now = performance.now();
			if (now - lastLevelEventAtRef.current > IDLE_TIMEOUT_MS) {
				return;
			}

			const meter = meterRef.current;
			if (meter) {
				const fill = meter.querySelector<HTMLElement>(
					'[role="progressbar"] > div',
				);
				if (fill) {
					// freezing the bar. Do NOT write
					fill.style.transform = `scaleX(${Math.max(0, levelRef.current)})`;
				}
			}

			if (now - lastStateSyncAtRef.current >= LEVEL_STATE_SYNC_INTERVAL_MS) {
				lastStateSyncAtRef.current = now;
				setLevel((prev) =>
					prev === levelRef.current ? prev : levelRef.current,
				);
				setPeak((prev) => (prev === peakRef.current ? prev : peakRef.current));
			}

			frameRef.current = requestAnimationFrame(animate);
		};

		const wake = () => {
			if (frameRef.current !== null) return;
			frameRef.current = requestAnimationFrame(animate);
		};
		wakeRef.current = wake;

		wake();

		return () => {
			if (frameRef.current !== null) {
				cancelAnimationFrame(frameRef.current);
				frameRef.current = null;
			}
			wakeRef.current = null;
		};
	}, [meterRef, playingRef, testRunningRef]);

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
					levelRef.current = levelData.level;
				}
				if (typeof levelData.peak === "number") {
					peakRef.current = levelData.peak;
				}
				lastLevelEventAtRef.current = performance.now();
				wakeRef.current?.();
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
