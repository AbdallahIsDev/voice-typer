// Data hook for the Microphone page.
//
// Owns: the ``microphones`` / ``config`` / ``loading`` / ``loadError``
// / ``refreshing`` state, the module-level caches
// (``_cachedMicrophones`` / ``_cachedConfig``) that survive page
// navigations, the ``loadData`` / ``handleManualRefresh`` /
// ``updateConfig`` handlers, the mount-time load effect, and the
// ``config_changed`` + ``microphones_changed`` event subscriptions.
//
// The ``microphones_changed`` handler depends on the test hook's
// ``selectMicrophone`` closure (for the auto-fallback-to-default
// behaviour when the active mic is hot-unplugged). The page owns a
// shared ``selectMicrophoneRef`` and passes it to both this hook and
// ``useMicrophoneTest``; this hook reads ``selectMicrophoneRef.current``
// at event-fire time so it always invokes the latest closure without
// re-subscribing on every render (PVT-035 / Fix 2 pattern).

import {
	type Dispatch,
	type MutableRefObject,
	type SetStateAction,
	useCallback,
	useEffect,
	useState,
} from "react";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";

// Module-level cache — persists across page navigations so microphone
// settings render instantly on re-visit instead of showing a loading
// spinner.
let _cachedMicrophones: MicrophoneDevice[] = [];
let _cachedConfig: VoiceTyperConfig | null = null;

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
	refreshing: boolean;
	agoLabel: string;
	loadData: (isCancelled?: () => boolean) => Promise<void>;
	handleManualRefresh: () => Promise<void>;
	updateConfig: (updates: Partial<VoiceTyperConfig>) => void;
}

export function useMicrophoneData({
	selectMicrophoneRef,
}: UseMicrophoneDataOptions): UseMicrophoneDataResult {
	const { call } = usePython();
	const { showSnack } = useSnackbar();
	const { agoLabel, markUpdated } = useLastUpdated();

	const [microphones, setMicrophones] =
		useState<MicrophoneDevice[]>(_cachedMicrophones);
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	// F4 (b-review Finding 11): "Last updated" indicator state. The
	// module-level caches (``_cachedMicrophones``, ``_cachedConfig``)
	// survive page navigations, so we mark the timestamp after each
	// successful loadData() to surface staleness to the user.
	const [refreshing, setRefreshing] = useState(false);
	const [loading, setLoading] = useState(true);
	// NF-R10-2: surface backend-load failures to the user instead of
	// silently masking them. The previous implementation only logged to
	// console, leaving the user with an empty mic list and no indication
	// that the backend was unreachable (vs. genuinely no microphones).
	const [loadError, setLoadError] = useState<string | null>(null);

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
				console.warn("[IPC] microphone command failed: set_config:", err);
			});
		},
		[call],
	);

	// PVT-G5-054 (session 5): ``isCancelled`` is consulted after every
	// ``await`` so an unmounted component (or a stale invocation
	// superseded by a newer ``loadData`` call) does not have its
	// in-flight ``setX`` calls land on a dead or stale React state. The
	// default ``() => false`` keeps existing callers (the
	// ``microphones_changed`` hot-swap handler, ``handleManualRefresh``)
	// working without changes.
	const loadData = useCallback(
		async (isCancelled: () => boolean = () => false) => {
			setLoading(true);
			// NF-R10-2: clear any prior load error before retrying so
			// the EmptyState swaps back to the spinner during the retry
			// attempt.
			setLoadError(null);
			try {
				const [mics, cfg] = await Promise.all([
					call<MicrophoneDevice[]>("get_microphones"),
					call<VoiceTyperConfig>("get_config"),
				]);
				if (isCancelled()) return;
				_cachedMicrophones = Array.isArray(mics) ? mics : [];
				_cachedConfig = cfg;
				// PVT-G5-054 (session 5): don't touch state if we were cancelled.
				if (!isCancelled()) {
					setMicrophones(_cachedMicrophones);
					setConfig(cfg);
				}
			} catch (err) {
				console.error("Failed to load microphone data:", err);
				// NF-R10-2: capture the error message so the render
				// path can show a retry EmptyState instead of an
				// ambiguous empty list.
				if (!isCancelled()) {
					setLoadError(
						err instanceof Error
							? err.message
							: "Failed to load microphone data",
					);
				}
			} finally {
				if (!isCancelled()) setLoading(false);
				// F4: bump the "last updated" timestamp after each load attempt.
				markUpdated();
			}
		},
		[call, markUpdated],
	);

	// PVT-G5-054 (session 5): guard the mount-time load against
	// setState-after-unmount (and against superseding calls from
	// handleManualRefresh or the microphones_changed hot-swap handler)
	// via a local ``cancelled`` flag captured by the ``() => cancelled``
	// closure passed into ``loadData``.
	useEffect(() => {
		let cancelled = false;
		loadData(() => cancelled);
		return () => {
			cancelled = true;
		};
	}, [loadData]);

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	// Wraps ``loadData()`` so we can flip a ``refreshing`` flag for the
	// button's spinner state without disturbing ``loading`` (which gates
	// the page's main spinner on first visit).
	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await loadData();
		} finally {
			setRefreshing(false);
		}
	}, [loadData]);

	// F11-FIX (b-review Finding 11): invalidate the module-level caches
	// when the backend reports that the underlying data changed through
	// a path OUTSIDE this page. The server already emits
	// ``microphones_changed`` (startup_tasks.py) when the device list
	// changes; without subscribing here, ``_cachedMicrophones`` would
	// show stale devices until the next manual refresh. ``config_changed``
	// (emitted by set_config / onboarding) keeps ``_cachedConfig`` fresh
	// too. Both re-run loadData() so the cache AND the visible UI update
	// — the "Last updated" indicator is only the safety net, not the
	// sole invalidation path.
	//
	// PVT-035 (Fix 2): hot-swap detection. After loadData() refreshes
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
					_cachedMicrophones.some(
						(m) => (m.id ?? String(m.index)) === previousMicId,
					);
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
		useCallback((): (() => void) | undefined => {
			void loadData();
			return undefined;
		}, [loadData]),
	);

	return {
		microphones,
		config,
		setConfig,
		loading,
		loadError,
		refreshing,
		agoLabel,
		loadData,
		handleManualRefresh,
		updateConfig,
	};
}
