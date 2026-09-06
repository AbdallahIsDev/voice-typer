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
// start / stop / mic-change (rare, sub-Hz events). The rAF loop ALSO
// throttles the latest ref values into React state at a low fixed
// cadence (``LEVEL_STATE_SYNC_INTERVAL_MS``, ~8 Hz) — state-bound
// consumers that are NOT the bar fill depend on it:
// ``ActiveMicrophoneCard``'s "Level: NN%" text, ``LevelBar``'s
// ``aria-valuenow``/``aria-valuetext`` (the accessible value AT reads),
// the clipping indicator, and ``LiveQualityFeedback``'s voice/tier
// feedback. Without this sync those consumers freeze at the last reset
// while only the bar fill moves. ~8 Hz keeps the parent re-render cost
// far below the old 30 Hz setState-per-push path while staying well
// above what a text readout / SR announcement needs.
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
import { useLatestRef } from "@/hooks/useLatestRef";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import {
	CONSENT_REQUIRED_CODE,
	VOICE_BIOMETRIC_CONSENT_FIELD,
} from "@/lib/consent";
import type { VoiceTyperConfig } from "@/types/config";

// Boot-race recovery for ``level_monitor_start``: on a cold start with
// the Microphone page restored, the mount effect fires the moment the
// config round-trip lands — which can be while the host bridge is still
// establishing (renderer connects before the backend finishes booting).
// A rejected/failed start used to be terminal (console warn only),
// leaving the live level bar dead for the page's entire lifetime.
// Bounded backoff retry so the meter self-heals; a genuine backend
// refusal surfaces through the consent path or the warn log.
const START_RETRY_DELAYS_MS: readonly number[] = [1000, 2000, 4000];

// ──  start/stop ownership sequence (module-scoped) ──
//
// Monotonic counters tracking ``level_monitor_start`` issuances and the
// ``level_monitor_stop`` calls that have claimed them, shared by every
// effect run of this hook. Module scope (not a per-instance ref) is
// deliberate: a full page remount creates a NEW hook instance while the
// PREVIOUS instance's in-flight start IPC may still resolve afterwards,
// and the backend level monitor is a single global stream — so "is this
// issuance still the newest one" and "has this issuance already been
// matched with a stop" must be answerable across hook instances.
let monitorStartIssuedSeq = 0;
let monitorStopClaimedSeq = 0;

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
	/**
	 * When true, the level monitor is force-paused: the mount effect
	 * skips ``level_monitor_start`` and sends ``level_monitor_stop``
	 * instead. Used by the Microphone page while the active device is
	 * lost (``device_lost`` push event) — monitoring a vanished stream
	 * is futile, so the page pauses the meter until the user retries
	 * (flipping this back to false re-runs the effect and restarts
	 * monitoring).
	 */
	paused?: boolean;
	/**
	 * Optional callback invoked when ``level_monitor_start`` is refused
	 * with the backend's ``client.consent_required`` envelope (a race:
	 * consent revoked between the renderer's client-side gate and the
	 * IPC, or a stale renderer). Receives the envelope's
	 * ``consent_field`` so the caller can surface a consent snackbar
	 * with a deep-link to the exact Settings toggle. Without this the
	 * refusal is only console.warn'd — silent from the user's
	 * perspective.
	 *
	 * MUST be referentially stable (wrap in ``useCallback``): it is a
	 * dependency of the mount effect, so an identity change tears down
	 * (``level_monitor_stop``) and restarts the level monitor.
	 */
	onConsentRequired?: (consentField?: string) => void;
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

// ──  LevelBar fill colour ──────────────────────────────────────
//
// The fill is styled SOLID ``bg-primary`` by ``LevelBar.tsx`` (a class,
// not an inline style), so the rAF loop below must NOT write
// ``backgroundColor`` — only ``transform: scaleX()``. The former
// per-tier colour ladder (and its duplicated private ``getLevelColor``
// copy here) was removed when the fill became solid primary; the tier
// is communicated via aria-valuetext + the ⚠ clipping icon instead.

export function useMicrophoneLevelMonitor({
	config,
	playingRef,
	testRunningRef,
	meterRef,
	paused = false,
	onConsentRequired,
}: UseMicrophoneLevelMonitorOptions): UseMicrophoneLevelMonitorResult {
	const { call } = usePython();

	// Ref mirror of `call` so the level-monitor lifecycle effect depends
	// only on the mic/consent config. Test mocks may return a FRESH call
	// per render — an effect dep on it re-fires level_monitor_start +
	// the one-shot poll (→ setLevel → re-render → new call → loop). Same
	// pattern as useVocabulary.ts.
	const callRef = useLatestRef(call);

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
	// rAF loop reads these refs, writes the latest values to the DOM
	// imperatively, and throttles them into React state at ~8 Hz (see
	// ``LEVEL_STATE_SYNC_INTERVAL_MS`` below). The push handler itself
	// never calls setState, so the parent doesn't re-render at 30 Hz.
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
	//
	// ``paused`` (device-lost gate): while the page reports the active
	// microphone as lost, skip the start entirely AND send an explicit
	// stop — the backend stream for a vanished device is dead weight.
	// Flipping ``paused`` back to false re-runs this effect, which
	// restarts monitoring without any extra imperative plumbing.
	// (The ``level_monitor_start`` boot-race retry schedule lives in the
	// module-level ``START_RETRY_DELAYS_MS`` above.)
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	useEffect(() => {
		// Send ``level_monitor_stop`` unless a stop for an equal-or-newer
		// start issuance was already claimed (by a previous paused
		// teardown, another run's cleanup, or a deferred in-flight-start
		// teardown). A claim at seq N means every start issuance ≤ N has
		// been matched with a stop — the backend serialises command
		// handling under its dispatch lock, so a stop claimed at N also
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
			// Stop whatever any effect run may have started — including a
			// start IPC still in flight (its resolution-time teardown below
			// sees this claim and stays quiet instead of double-stopping).
			sendStopClaiming(monitorStartIssuedSeq);
			return;
		}
		// Privacy gate (GDPR Art. 9): the level monitor opens a
		// continuous biometric-capture InputStream on the mic — the
		// backend enforces ``voice_biometric_consent`` and refuses
		// ``level_monitor_start`` without it. Skip the start + the
		// one-shot poll client-side when consent is off, so the page
		// doesn't spam futile IPC calls + console warnings on every
		// mount for users who haven't granted consent yet (they get a
		// consent prompt when they try to START a test instead).
		if (!config?.voice_biometric_consent) return;
		const micId = config?.microphone ?? null;

		// Privacy gate: do not start monitoring while the document is
		// hidden (background/autostart). The Microphone page may be
		// restored from persisted navigation (vt_nav_state) while the
		// window is still hidden (VT_START_HIDDEN=1). Starting the level
		// monitor in that state would activate the OS mic indicator
		// invisibly. Defer until the page is actually visible.
		const isHiddenAtStart =
			typeof document !== "undefined" && document.visibilityState !== "visible";

		// Bounded-retry state for THIS effect run. ``cancelled`` flips in
		// the cleanup below (dep change / unmount) so an in-flight retry
		// chain never outlives its effect instance.
		let cancelled = false;
		let retryTimer: ReturnType<typeof setTimeout> | null = null;
		let attempt = 0;
		// Whether THIS effect instance actually got the monitor running.
		// Set in the start's `.then` only when not cancelled. The cleanup
		// only sends ``level_monitor_stop`` when this is true —
		// otherwise React StrictMode's dev double-invocation (mount →
		// cleanup → mount) would stop the monitor run 1 just started,
		// then run 2 restarts it: the `[LEVEL-MON] Monitoring started /
		// stopped / started` bounce in voice-typer.log on EVERY page
		// mount (the user's "start, then stop, then start again"
		// report). Run 1's cleanup runs synchronously while its start
		// IPC is still in flight, so `startedHere` is false → the stop
		// is skipped; run 2's start then finds the stream already
		// active ("Already monitoring" — a backend no-op) and run 2's
		// own cleanup owns the real unmount stop.
		let startedHere = false;
		// Sequence token of the start issuance THIS run last made
		// (0 = none yet — hidden-deferred path, consent gate). Read by the
		// start's ``.then`` and the cleanup to match a stop against the
		// exact issuance it owns.
		let issuedStartSeq = 0;
		let deferredVisibleCleanup: (() => void) | null = null;

		const startMonitor = (): void => {
			// Stamp THIS issuance so the resolution path can tell whether a
			// later effect run has since issued its own start (which then
			// owns the shared backend stream).
			issuedStartSeq = ++monitorStartIssuedSeq;
			callRef
				.current<{ success: boolean }>("level_monitor_start", {
					mic_id: micId,
				})
				.then(() => {
					// The start succeeded. Only claim ownership when this
					// effect instance is still live — a StrictMode cleanup
					// (or a real unmount) that ran while the IPC was in
					// flight sets `cancelled`, so the cleanup must NOT stop
					// a monitor the NEW effect run is about to take over.
					if (!cancelled) {
						startedHere = true;
						return;
					}
					// Teardown raced the in-flight start: this run was
					// cancelled before its start resolved, so the cleanup ran
					// with `startedHere` still false and skipped the stop. If
					// no later run has issued its own start — which would then
					// own the shared stream — this run's stream is now
					// ownerless and MUST be stopped here: the OS mic indicator
					// would otherwise stay lit with no page active. The stop
					// is deliberately issued from here, after the start has
					// settled, rather than from the cleanup: a stop sent while
					// the start IPC was still in flight could not know whether
					// the backend stream would actually open. A later run's
					// start (the seq moved on) suppresses this stop — that run
					// owns the stream and its own teardown stops it.
					if (monitorStartIssuedSeq === issuedStartSeq) {
						sendStopClaiming(issuedStartSeq);
					}
				})
				.catch((err) => {
					// The backend's ``client.consent_required`` envelope
					// (ConsentRequiredError with consent_field) surfaces
					// only in a race — the client-side gate above normally
					// short-circuits before the IPC. Surface it through the
					// caller's deep-link snackbar instead of swallowing it.
					// A consent refusal is terminal for this effect run —
					// the dialog's onAllow restarts the monitor explicitly.
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
					// Boot-window recovery: retry on a short backoff unless
					// the effect was torn down or the retry budget is spent.
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
		// One-shot fallback poll (shared by both start paths). The backend's
		// ``mic_level`` push is coalesced at ≤30 Hz, so the first frame may
		// take up to ~33 ms to arrive after ``level_monitor_start``. We issue
		// a single ``microphone_test_get_level`` call to seed the UI
		// immediately; subsequent updates come from the push event
		// subscription below. The fallback is a no-op if the push event
		// arrives first (the setState calls are idempotent — last write wins).
		//
		// The one-shot poll still calls ``setLevel`` / ``setPeak`` because it
		// runs ONCE per start — a single React re-render is fine (and
		// desirable, so the initial state isn't stale). The high-frequency
		// ``mic_level`` push handler below is the path that must avoid
		// setState, and it does.
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
					// Non-fatal — the push event subscription will still
					// deliver updates once the backend starts publishing.
					console.warn(
						"[renderer:useMicrophoneLevelMonitor] one-shot level poll failed:",
						e,
					);
				}
			})();
		};
		// If the document is hidden at mount (background/autostart), defer
		// the start until the page becomes visible. This prevents the OS
		// mic indicator from appearing while the window is still hidden in
		// the background due to restored persisted navigation.
		//
		// NOTE: this branch must NOT early-return its own listener-only
		// cleanup — the deferred start must fall through to the shared
		// ``startedHere``-guarding cleanup below so the monitor started on
		// visibility is stopped on unmount. A separate cleanup that only
		// removes the listener would leak the InputStream (the OS mic
		// indicator would stay lit after navigating away with no page
		// active).
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
					// Trigger one-shot poll now that we're visible.
					runOneShotLevelPoll();
				}
			};
			document.addEventListener("visibilitychange", onVisible);
			deferredVisibleCleanup = () =>
				document.removeEventListener("visibilitychange", onVisible);
		} else {
			startMonitor();
			// One-shot fallback poll to seed the UI immediately (shared
			// helper above). The deferred branch runs the same poll from
			// ``onVisible`` once it actually becomes visible.
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
			// Only stop a monitor THIS effect actually started.
			// StrictMode's dev double-invocation runs the cleanup
			// synchronously while the first effect's start IPC is still
			// in flight (startedHere=false), so the cleanup skips the
			// stop — the SECOND effect run will find the stream already
			// active ("Already monitoring") instead of stopping then
			// restarting it. On a real unmount (or a dep change), the
			// start has already resolved with startedHere=true, so the
			// stop is sent correctly. An unmount that beats the
			// in-flight start (startedHere still false) is handled by
			// the start's own resolution-time teardown above.
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
	]);

	//  rAF loop — imperative DOM writes for the LevelBar fill.
	//
	// Mirrors the bubble's ``useAudioLevels`` rAF pattern
	// (``useAudioLevels.ts:262-317``), including the "wake-on-event"
	// scheduling gate (``useAudioLevels.ts:286-305``): the loop is
	// (re)armed by an explicit ``wake()`` call instead of unconditionally
	// re-scheduling on every frame.
	//
	// Previously the rAF callback unconditionally re-scheduled
	// the next frame on EVERY gate-closed branch (hidden / not
	// monitoring / playing) "so the loop can react to gate flips without
	// a remount". But the Microphone page is commonly mounted while the
	// user is NOT actively testing / monitoring — they just navigated to
	// the page to read / scroll. The loop ticked at ~60 Hz doing 3 ref
	// reads + visibility check + a no-op reschedule, keeping the
	// renderer's compositing thread awake on battery-constrained
	// laptops. Browsers throttle rAF in HIDDEN tabs (~1 Hz) but DON'T
	// throttle it when the tab is VISIBLE and the page is just idle — so
	// the cost was real on visible tabs.
	//
	// Fix: adopt the bubble's wake-on-event pattern.
	//   - The ``mic_level`` push handler (below) updates
	//     ``lastLevelEventAtRef.current`` on every event and calls
	//     ``wakeRef.current?.()`` to (re)arm the loop.
	//   - The rAF callback checks ``performance.now() -
	//     lastLevelEventAtRef.current > IDLE_TIMEOUT_MS`` (500ms). If
	//     idle, it returns WITHOUT scheduling the next frame — the loop
	//     pauses. The next ``mic_level`` event re-arms via ``wake()``.
	//   - Gate-closed branches (hidden / not monitoring / playing) also
	//     return WITHOUT rescheduling. The next ``mic_level`` event
	//     (which arrives at ≤30 Hz from the backend when the gate is
	//     open) re-arms via ``wake()``. When the gate is closed the push
	//     handler suppresses wake too, so the loop stays paused — no
	//     idle spinning.
	//   - On mount, ``lastLevelEventAtRef.current`` is primed to
	//     ``performance.now()`` and ``wake()`` is called once so the
	//     loop runs for at least the first 500ms (giving the backend
	//     time to start publishing ``mic_level`` events after
	//     ``level_monitor_start``). If real events arrive within 500ms
	//     (the normal case), the loop continues. If none arrive (e.g.
	//     mic is muted, no audio input, or backend stalls), the loop
	//     pauses after 500ms — no continuous spin.
	//
	// Visual parity: the React-driven path updates ``LevelBar``'s fill via
	// the inline ``transform: scaleX()`` style prop (the fill colour is a
	// static ``bg-primary`` class). This rAF loop writes the SAME property
	// to the same DOM node at ≤60 Hz while events are flowing — strictly
	// smoother than the 30 Hz React-driven cadence, with the same visual
	// result.
	// When the loop is paused (idle / gate closed), the bar holds its
	// last value — which matches the prior behaviour (the React state
	// was already stale during monitoring; only the rAF-driven DOM write
	// was live).
	//  the rAF loop writes ``transform: scaleX()`` — LevelBar animates its
	// fill via transform (see the PERF note in LevelBar.tsx), so writing
	// ``width`` here would double-scale / freeze the bar.
	const lastLevelEventAtRef = useRef(0);
	const frameRef = useRef<number | null>(null);
	const wakeRef = useRef<(() => void) | null>(null);
	// Last time the rAF loop synced ``levelRef`` / ``peakRef`` into
	// React state. Reset to 0 on every effect run so the first active
	// frame publishes immediately after a gate flip.
	const lastStateSyncAtRef = useRef(0);
	const IDLE_TIMEOUT_MS = 500;
	// Cadence for ref→state sync. ~8 Hz: fast enough that the "Level:
	// NN%" text, the LevelBar's aria value, the clipping indicator and
	// LiveQualityFeedback's tier feel live; slow enough that the parent
	// Microphone page subtree re-renders 8×/s instead of the 30 Hz the
	// old setState-per-push path cost.
	const LEVEL_STATE_SYNC_INTERVAL_MS = 120;

	useEffect(() => {
		// Prime the timestamp so the first frame runs (gives the
		// backend time to start publishing ``mic_level`` events within
		// the 500ms idle window). Without this, the very first frame
		// would see ``now - 0 > 500`` and immediately pause — leaving
		// the LevelBar at 0% even though the one-shot poll had already
		// seeded ``levelRef.current``.
		lastLevelEventAtRef.current = performance.now();
		lastStateSyncAtRef.current = 0;

		const animate = () => {
			frameRef.current = null;
			// Gate: skip DOM writes when hidden / not monitoring / playing.
			// Do NOT reschedule — the next ``mic_level`` event (which
			// arrives at ≤30 Hz when the gate is open) re-arms via
			// ``wake()``. When the gate is closed the push handler
			// suppresses wake too, so the loop stays paused instead of
			// spinning at 60 Hz doing nothing (wake-on-event fix).
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

			// Idle pause: if no ``mic_level`` event has arrived
			// within ``IDLE_TIMEOUT_MS``, pause the loop. The next event
			// re-arms via ``wake()``.
			const now = performance.now();
			if (now - lastLevelEventAtRef.current > IDLE_TIMEOUT_MS) {
				return;
			}

			const meter = meterRef.current;
			if (meter) {
				// LevelBar's fill div — ``[role="progressbar"] > div``.
				// Selector mirrors ``LevelBar.tsx``'s render structure
				// (the ``<div role="progressbar">`` wrapper + its single
				// child ``<div>`` carrying the inline ``transform``
				// style). If ``LevelBar``'s DOM changes, update this
				// selector.
				const fill = meter.querySelector<HTMLElement>(
					'[role="progressbar"] > div',
				);
				if (fill) {
					// Write the SAME style property LevelBar renders:
					// the fill is a full-width solid-primary div animated
					// via ``transform: scaleX()`` (compositor-friendly —
					// see the PERF note in LevelBar.tsx). Writing
					// ``width`` here instead would fight the fill's
					// ``w-full`` class AND leave the transform at its
					// stale React-rendered value, double-scaling /
					// freezing the bar. Do NOT write
					// ``backgroundColor``: the fill colour is the static
					// ``bg-primary`` class, not an inline style.
					fill.style.transform = `scaleX(${Math.max(0, levelRef.current)})`;
				}
			}

			// Throttled ref→state sync for the state-bound consumers
			// (level text, aria value, clipping icon, quality tiers).
			// Runs INSIDE the same gated frame as the DOM write, so a
			// paused/idle loop never publishes stale values. Identical
			// values bail out via the functional updater (React skips
			// the re-render), so quiet audio costs nothing.
			if (now - lastStateSyncAtRef.current >= LEVEL_STATE_SYNC_INTERVAL_MS) {
				lastStateSyncAtRef.current = now;
				setLevel((prev) =>
					prev === levelRef.current ? prev : levelRef.current,
				);
				setPeak((prev) => (prev === peakRef.current ? prev : peakRef.current));
			}

			// Schedule the next frame. The idle check above will pause
			// the loop on the next frame if no new ``mic_level`` event has
			// arrived in the interim.
			frameRef.current = requestAnimationFrame(animate);
		};

		// ``wake`` function — idempotent (re)starter. Called from the
		// ``mic_level`` push handler on every event + once on mount. If a
		// frame is already scheduled, wake is a no-op (the in-flight
		// frame will pick up the latest ``lastLevelEventAtRef.current``
		// when it fires).
		const wake = () => {
			if (frameRef.current !== null) return;
			frameRef.current = requestAnimationFrame(animate);
		};
		wakeRef.current = wake;

		// Initial wake — starts the loop so the first frame can render
		// the level seeded by the one-shot poll above. If no real
		// ``mic_level`` events arrive within 500ms, the loop pauses after
		// the first frame window.
		wake();

		return () => {
			if (frameRef.current !== null) {
				cancelAnimationFrame(frameRef.current);
				frameRef.current = null;
			}
			wakeRef.current = null;
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
	// ``setPeak``. The rAF loop reads the refs, writes the DOM
	// imperatively at ≤60 Hz, and throttles the values into React state
	// at ~8 Hz (see ``LEVEL_STATE_SYNC_INTERVAL_MS``). ``setMicMonitoring``
	// is still called when ``active`` flips (rare, sub-Hz) because the
	// ``micMonitoring`` flag drives the "Monitoring…" / "Level: X%"
	// label toggle in ``ActiveMicrophoneCard``, which DOES need a
	// re-render to update.
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
				// Wake-on-event: mark that a ``mic_level`` event
				// arrived and (re)arm the rAF loop. If the loop was paused
				// due to idle (no events for > 500ms), this resumes it. If
				// a frame is already scheduled, ``wake()`` is a no-op (the
				// in-flight frame will pick up the updated
				// ``lastLevelEventAtRef.current`` when it fires).
				lastLevelEventAtRef.current = performance.now();
				wakeRef.current?.();
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
