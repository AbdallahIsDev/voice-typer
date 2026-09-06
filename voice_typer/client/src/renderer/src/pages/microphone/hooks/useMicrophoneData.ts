// Data hook for the Microphone page.
//
// Owns: the ``microphones`` / ``config`` / ``loading`` / ``loadError``
// state, the module-level caches (``_cachedMicrophones`` /
// ``_cachedConfig``) that survive page navigations, the ``loadData`` /
// ``updateConfig`` handlers, the mount-time load effect, and the
// ``config_changed`` + ``microphones_changed`` event subscriptions.
//
// Both the mount/reload path and the ``microphones_changed`` handler
// reconcile the active microphone selection: when the persisted
// ``config.microphone`` id matches no enumerated device (hot-unplug,
// Bluetooth power-off, renamed host API), the hook auto-falls back to
// System Default with a warning snack instead of rendering
// ``t("microphone.unknown")``.
//
// The reconciliation depends on the test hook's ``selectMicrophone``
// closure. The page owns a shared ``selectMicrophoneRef`` and passes
// it to both this hook and ``useMicrophoneTest``; this hook reads
// ``selectMicrophoneRef.current`` at call time so it always invokes
// the latest closure without re-subscribing on every render.

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
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";

// Module-level cache — persists across page navigations so microphone
// settings render instantly on re-visit instead of showing a loading
// spinner.
let _cachedMicrophones: MicrophoneDevice[] = [];
let _cachedConfig: VoiceTyperConfig | null = null;

// Boot-race recovery: on cold start the renderer can connect (and this
// page can fetch ``get_microphones``) BEFORE the backend's startup
// mic-enumeration task has populated its device registry — the fetch
// legitimately resolves to ``[]`` and, historically, nothing re-notified
// the page afterwards (the backend skipped the first empty → populated
// ``microphones_changed`` publish). Retry the load on a short bounded
// backoff so the list self-heals; a machine that genuinely has zero
// microphones simply stops after the last attempt.
const EMPTY_LIST_RETRY_DELAYS_MS: readonly number[] = [1000, 2000, 4000, 8000];

/** Stable device id comparison (backend ids are "<hostapi>|<name>[#N]"). */
function deviceMatches(device: MicrophoneDevice, micId: string): boolean {
	return (device.id ?? String(device.index)) === micId;
}

type MicReconcileDecision =
	| { action: "rearm" }
	| { action: "fallback"; micId: string }
	| { action: "noop" };

/**
 * Pure active-mic reconciliation decision, shared by the full
 * ``loadData`` path and the lightweight ``config_changed`` path (E7:
 * one decision implementation, not two copies of the guard logic).
 *
 * Mirrors the historical inline rules exactly: nothing to do when no
 * mic is selected, the list is empty, or the id is already guarded;
 * re-arm the guard when the id resolves again; fall back only when a
 * persisted id matches no enumerated device. Side effects (snack +
 * select) stay with the callers.
 */
function decideMicReconcile(
	cfg: VoiceTyperConfig | null,
	mics: readonly MicrophoneDevice[],
	lastMissingId: string | null,
): MicReconcileDecision {
	const activeMicId = cfg?.microphone ?? null;
	if (activeMicId === null || mics.length === 0) {
		return { action: "noop" };
	}
	if (lastMissingId === activeMicId) {
		return { action: "noop" };
	}
	if (mics.some((m) => deviceMatches(m, activeMicId))) {
		return { action: "rearm" };
	}
	return { action: "fallback", micId: activeMicId };
}

interface UseMicrophoneDataOptions {
	/**
	 * Ref-to-latest ``selectMicrophone`` closure owned by
	 * ``useMicrophoneTest``. Read at event-fire time by the
	 * ``microphones_changed`` handler so we don't need to re-subscribe
	 * on every render.
	 */
	selectMicrophoneRef: MutableRefObject<
		(micId: string | null) => Promise<void>
	>;
}

export interface UseMicrophoneDataResult {
	microphones: MicrophoneDevice[];
	config: VoiceTyperConfig | null;
	setConfig: Dispatch<SetStateAction<VoiceTyperConfig | null>>;
	loading: boolean;
	loadError: string | null;
	loadData: (isCancelled?: () => boolean) => Promise<void>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
}

export function useMicrophoneData({
	selectMicrophoneRef,
}: UseMicrophoneDataOptions): UseMicrophoneDataResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();

	// callRef / showSnackRef mirrors (Home.tsx pattern): `loadData`
	// must keep a STABLE identity ([] deps) so the mount effect below
	// (and the event-handler callbacks that depend on it) don't re-fire
	// on an identity change. Both are useCallback-stable in production,
	// but test mocks return FRESH functions per render — depending on
	// them re-fires the mount load (get_microphones/get_config →
	// setState → re-render → new call → loop → worker OOM). The mirrors
	// keep the refs fresh; the effect deps stay identity-free.
	const callRef = useLatestRef(call);
	const showSnackRef = useRef(showSnack);
	useEffect(() => {
		showSnackRef.current = showSnack;
	}, [showSnack]);

	const [microphones, setMicrophones] =
		useState<MicrophoneDevice[]>(_cachedMicrophones);
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	const [loading, setLoading] = useState(true);
	//surface backend-load failures to the user instead of
	// silently masking them. The previous implementation only logged to
	// console, leaving the user with an empty mic list and no indication
	// that the backend was unreachable (vs. genuinely no microphones).
	const [loadError, setLoadError] = useState<string | null>(null);

	// Last persisted id we already auto-fell-back from. Guards against
	// snack-spam: until the backend confirms the switch (or the device
	// reappears / the user picks another mic), repeated reloads of the
	// SAME stale id must not re-fire the fallback.
	const lastMissingMicIdRef = useRef<string | null>(null);

	// Empty-list retry state (see EMPTY_LIST_RETRY_DELAYS_MS above).
	// ``unmountedRef`` also guards the scheduled retry against
	// setState-after-unmount; the timer is cleared on unmount.
	const unmountedRef = useRef(false);
	const emptyRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const emptyRetryAttemptsRef = useRef(0);
	useEffect(() => {
		return () => {
			unmountedRef.current = true;
			if (emptyRetryTimerRef.current !== null) {
				clearTimeout(emptyRetryTimerRef.current);
				emptyRetryTimerRef.current = null;
			}
		};
	}, []);

	/** Optimistic config update: writes through to backend + local cache. */
	const updateConfig = useCallback(
		(updates: Partial<VoiceTyperConfig>) => {
			setConfig((prev) => {
				if (!prev) return prev;
				const next = { ...prev, ...updates };
				_cachedConfig = next;
				return next;
			});
			call("set_config", updates).catch((err) => {
				console.warn(
					"[renderer:useMicrophoneData] microphone command failed: set_config:",
					err,
				);
			});
		},
		[call],
	);

	// ``isCancelled`` is consulted after every ``await`` so an unmounted
	// component (or a stale invocation superseded by a newer ``loadData``
	// call) does not have its in-flight ``setX`` calls land on a dead or
	// stale React state. The default ``() => false`` keeps existing
	// callers (the ``microphones_changed`` hot-swap handler) working
	// without changes.
	// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
	const loadData = useCallback(
		async (isCancelled: () => boolean = () => false) => {
			/**
			 * Startup/reload counterpart of the ``microphones_changed``
			 * hot-swap handler below: a persisted device id that matches no
			 * enumerated device would otherwise leave ActiveMicrophoneCard
			 * rendering ``t("microphone.unknown")`` silently until some later
			 * event fired. Runs the SAME fallback (warning snack +
			 * selectMicrophone(null)). Guard rails:
			 *   - skip while already on System Default (nothing to fall back from),
			 *   - skip when the list is EMPTY — a failed/empty enumeration is
			 *     not evidence the device is gone (the catch path never gets
			 *     here, but an empty success response does),
			 *   - fire at most once per distinct missing id until state changes
			 *     (loop guard: config_changed reloads triggered BY the fallback
			 *     itself must not re-snack while the stale id is still persisted).
			 */
			function reconcileActiveMic(cfg: VoiceTyperConfig | null): void {
				const decision = decideMicReconcile(
					cfg,
					_cachedMicrophones,
					lastMissingMicIdRef.current,
				);
				if (decision.action === "rearm") {
					// Device is back (or selection is valid again) — re-arm the
					// guard so a FUTURE disappearance of the same id falls back.
					lastMissingMicIdRef.current = null;
					return;
				}
				if (decision.action === "noop") {
					return;
				}
				lastMissingMicIdRef.current = decision.micId;
				showSnackRef.current(t("microphone.activeMicUnavailable"), "warning");
				// Auto-fallback to system default. selectMicrophone(null)
				// already handles stopping any active test, clearing the
				// config, and showing a confirmation snackbar.
				selectMicrophoneRef
					.current(null)
					.catch((err) =>
						console.warn(
							"[renderer:useMicrophoneData] auto-fallback to system default failed:",
							err,
						),
					);
			}

			setLoading(true);
			//clear any prior load error before retrying so
			// the EmptyState swaps back to the spinner during the retry
			// attempt.
			setLoadError(null);
			try {
				const [mics, cfg] = await Promise.all([
					callRef.current<MicrophoneDevice[]>("get_microphones"),
					callRef.current<VoiceTyperConfig>("get_config"),
				]);
				if (isCancelled()) return;
				_cachedMicrophones = Array.isArray(mics) ? mics : [];
				_cachedConfig = cfg;
				if (!isCancelled()) {
					setMicrophones(_cachedMicrophones);
					setConfig(cfg);
					reconcileActiveMic(cfg);
					// Boot-race recovery: an empty list on a successful
					// fetch is usually the backend's device registry not
					// being populated yet (the renderer connected before
					// the startup enumeration ran), not a machine with no
					// microphones. Schedule one bounded backoff retry —
					// the attempt counter stops the loop after the last
					// delay, and a non-empty result re-arms it for the
					// next cold start.
					if (_cachedMicrophones.length === 0) {
						const attempts = emptyRetryAttemptsRef.current;
						if (
							attempts < EMPTY_LIST_RETRY_DELAYS_MS.length &&
							emptyRetryTimerRef.current === null &&
							!unmountedRef.current
						) {
							emptyRetryTimerRef.current = setTimeout(() => {
								emptyRetryTimerRef.current = null;
								emptyRetryAttemptsRef.current += 1;
								void loadData(() => unmountedRef.current);
							}, EMPTY_LIST_RETRY_DELAYS_MS[attempts]);
						}
					} else {
						emptyRetryAttemptsRef.current = 0;
					}
				}
			} catch (err) {
				console.error(
					"[renderer:useMicrophoneData] Failed to load microphone data:",
					err,
				);
				//capture the error message so the render
				// path can show a retry EmptyState instead of an
				// ambiguous empty list.
				if (!isCancelled()) {
					setLoadError(
						err instanceof Error
							? err.message
							: t("microphone.loadFailedDescription"),
					);
				}
			} finally {
				if (!isCancelled()) setLoading(false);
			}
		},
		[
			// The REF OBJECT, never ``.current``: the current closure is
			// assigned by ``useMicrophoneTest`` after mount and may change
			// identity across renders — depending on it re-fires the mount
			// load (the exact loop the render-loop guard pins). Reading
			// ``.current`` at call time already yields the latest closure.
			selectMicrophoneRef,
		],
	);

	//guard the mount-time load against
	// setState-after-unmount (and against superseding calls from the
	// microphones_changed hot-swap handler)
	// via a local ``cancelled`` flag captured by the ``() => cancelled``
	// closure passed into ``loadData``.
	useEffect(() => {
		let cancelled = false;
		loadData(() => cancelled);
		return () => {
			cancelled = true;
		};
	}, [loadData]);

	// F11-FIX (b-review Finding 11): invalidate the module-level caches
	// when the backend reports that the underlying data changed through
	// a path OUTSIDE this page. The server already emits
	// ``microphones_changed`` (startup_tasks.py) when the device list
	// changes; without subscribing here, ``_cachedMicrophones`` would
	// show stale devices until the next manual refresh. ``config_changed``
	// (emitted by set_config / onboarding) keeps ``_cachedConfig`` fresh
	// too. Both re-run loadData() so the cache AND the visible UI update.
	//
	//(Fix 2): hot-swap detection. After loadData() refreshes
	// the microphone list, if the currently-selected microphone
	// (config.microphone) is no longer present in the new list, we:
	//   1. show a warning snackbar explaining what happened, and
	//   2. auto-fall back to the system default (selectMicrophone(null)).
	// Previously the active mic card would silently render "Unknown" and
	// the user had no idea why their mic stopped working (USB disconnect,
	// Bluetooth headset power-off, hot-plug reorder, etc.).
	usePythonEvent(
		"microphones_changed",
		useCallback((): (() => void) | undefined => {
			void (async () => {
				const previousMicId = _cachedConfig?.microphone ?? null;
				await loadData();
				// ``_cachedMicrophones`` is updated synchronously by loadData()
				// before this point — read it directly so we don't depend on
				// the next React commit cycle.
				const stillPresent =
					previousMicId === null ||
					_cachedMicrophones.some((m) => deviceMatches(m, previousMicId));
				if (!stillPresent) {
					showSnack(t("microphone.activeMicUnavailable"), "warning");
					// Auto-fallback to system default. selectMicrophone(null)
					// already handles stopping any active test, clearing the
					// config, and showing a confirmation snackbar.
					await selectMicrophoneRef.current(null);
				}
			})();
			return undefined;
		}, [loadData, showSnack, selectMicrophoneRef]),
	);

	usePythonEvent(
		"config_changed",
		// biome-ignore lint/correctness/useExhaustiveDependencies: callRef is a useLatestRef mirror: reading .current in a stale closure is the hook's documented contract — .current must NOT become a dep
		useCallback((): (() => void) | undefined => {
			// Hot/cold split: a config echo (including the ones OUR OWN
			// updateConfig writes trigger) only needs the fresh config —
			// re-running the full loadData() here re-fired the native
			// PortAudio enumeration on every preset/filter/mic click, so
			// N rapid interactions queued N native enumerations. Fetch
			// get_config only and reconcile the selection against the
			// already-cached device list; real device deltas still arrive
			// via microphones_changed (untouched above). Escalate to a
			// full loadData() only when there is no device cache to
			// reconcile against (first load raced / cache cleared).
			void (async () => {
				let cfg: VoiceTyperConfig;
				try {
					cfg = await callRef.current<VoiceTyperConfig>("get_config");
				} catch (err) {
					console.error(
						"[renderer:useMicrophoneData] Failed to refresh config on config_changed:",
						err,
					);
					return;
				}
				_cachedConfig = cfg;
				setConfig(cfg);
				if (_cachedMicrophones.length === 0) {
					await loadData();
					return;
				}
				const decision = decideMicReconcile(
					cfg,
					_cachedMicrophones,
					lastMissingMicIdRef.current,
				);
				if (decision.action === "rearm") {
					lastMissingMicIdRef.current = null;
					return;
				}
				if (decision.action === "noop") {
					return;
				}
				lastMissingMicIdRef.current = decision.micId;
				showSnackRef.current(t("microphone.activeMicUnavailable"), "warning");
				try {
					await selectMicrophoneRef.current(null);
				} catch (err) {
					console.warn(
						"[renderer:useMicrophoneData] auto-fallback to system default failed:",
						err,
					);
				}
			})();
			return undefined;
			// selectMicrophoneRef (object, never `.current`): mirrors the
			// microphones_changed handler — depending on the ref object is
			// safe (stable identity); depending on `.current` would
			// re-subscribe on every closure change (render-loop hazard).
		}, [loadData, selectMicrophoneRef]),
	);

	return {
		microphones,
		config,
		setConfig,
		loading,
		loadError,
		loadData,
		updateConfig,
	};
}
