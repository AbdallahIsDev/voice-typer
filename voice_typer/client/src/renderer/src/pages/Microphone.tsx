import {
	AlertCircleIcon,
	Mic02Icon,
	MicOff01Icon,
	PlayIcon,
	Settings03Icon,
	StopIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { RangeSlider } from "@/components/common/RangeSlider";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LevelBar } from "@/components/feedback/LevelBar";
import { LiveQualityFeedback } from "@/components/feedback/LiveQualityFeedback";
import { Spinner } from "@/components/feedback/Spinner";
import {
	type AudioPreset,
	AudioPresetSelector,
} from "@/components/microphone/AudioPresetSelector";
import { MicrophoneListItem } from "@/components/microphone/MicrophoneListItem";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { Button } from "@/components/ui/button";
import { useLastUpdated } from "@/hooks/useLastUpdated";
import { usePython, usePythonEvent } from "@/hooks/usePython";
import { useSnackbar } from "@/hooks/useSnackbar";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { MicrophoneDevice, VoiceTyperConfig } from "@/types/config";

// Module-level cache — persists across page navigations so microphone settings
// render instantly on re-visit instead of showing a loading spinner.
let _cachedMicrophones: MicrophoneDevice[] = [];
let _cachedConfig: VoiceTyperConfig | null = null;

// ADR 0007: Preset → filter mapping is owned by the backend
// (voice_typer/server/audio_presets.py). The Microphone page just sends
// the selected preset name to set_config; the backend applies the
// individual filter toggles. No client-side PRESET_TO_FILTERS table.

/**
 * Build the noise-filter dict sent to microphone_test_start so the
 * backend's level_monitor.stop_test_recording can run the captured
 * audio through the same chain the user has configured.
 */
function buildTestFilters(
	config: VoiceTyperConfig | null,
): Record<string, unknown> {
	if (!config || config.audio_preset === "off") {
		return { noise_filter_enabled: false };
	}
	return {
		noise_filter_enabled: true,
		noise_filter_highpass: config.noise_filter_highpass ?? true,
		noise_filter_highpass_cutoff_hz:
			config.noise_filter_highpass_cutoff_hz ?? 80,
		noise_suppression_method: config.noise_suppression_method ?? "rnnoise",
		noise_filter_gate: config.noise_filter_gate ?? true,
		noise_filter_gate_open_threshold_db:
			config.noise_filter_gate_open_threshold_db ?? -26,
		noise_filter_gate_close_threshold_db:
			config.noise_filter_gate_close_threshold_db ?? -32,
		noise_filter_gate_attack_ms: config.noise_filter_gate_attack_ms ?? 25,
		noise_filter_gate_hold_ms: config.noise_filter_gate_hold_ms ?? 200,
		noise_filter_gate_release_ms: config.noise_filter_gate_release_ms ?? 150,
		noise_filter_eq: config.noise_filter_eq ?? true,
		noise_filter_eq_low_db: config.noise_filter_eq_low_db ?? -3,
		noise_filter_eq_mid_db: config.noise_filter_eq_mid_db ?? 3,
		noise_filter_eq_high_db: config.noise_filter_eq_high_db ?? 2,
		noise_filter_compressor: config.noise_filter_compressor ?? true,
		noise_filter_compressor_threshold_db:
			config.noise_filter_compressor_threshold_db ?? -18,
		noise_filter_compressor_ratio: config.noise_filter_compressor_ratio ?? 3,
		noise_filter_compressor_attack_ms:
			config.noise_filter_compressor_attack_ms ?? 6,
		noise_filter_compressor_release_ms:
			config.noise_filter_compressor_release_ms ?? 60,
		noise_filter_compressor_output_gain_db:
			config.noise_filter_compressor_output_gain_db ?? 0,
		noise_filter_limiter: config.noise_filter_limiter ?? true,
		noise_filter_limiter_ceiling_db:
			config.noise_filter_limiter_ceiling_db ?? -6,
		noise_filter_limiter_release_ms:
			config.noise_filter_limiter_release_ms ?? 60,
		noise_filter_notch: config.noise_filter_notch ?? false,
		noise_filter_notch_frequency_hz:
			config.noise_filter_notch_frequency_hz ?? 0,
	};
}

/**
 * Compute a stable string key from the audio-related config fields so
 * the page can detect "filters changed since last test" and prompt the
 * user to re-run the test.
 */
function computeAudioKey(config: VoiceTyperConfig | null): string {
	if (!config) return "";
	return JSON.stringify({
		preset: config.audio_preset,
		hp: config.noise_filter_highpass,
		hp_cut: config.noise_filter_highpass_cutoff_hz,
		method: config.noise_suppression_method,
		gate: config.noise_filter_gate,
		gate_open: config.noise_filter_gate_open_threshold_db,
		gate_close: config.noise_filter_gate_close_threshold_db,
		gate_attack: config.noise_filter_gate_attack_ms,
		gate_hold: config.noise_filter_gate_hold_ms,
		gate_release: config.noise_filter_gate_release_ms,
		eq: config.noise_filter_eq,
		eq_low: config.noise_filter_eq_low_db,
		eq_mid: config.noise_filter_eq_mid_db,
		eq_high: config.noise_filter_eq_high_db,
		comp: config.noise_filter_compressor,
		comp_thr: config.noise_filter_compressor_threshold_db,
		comp_ratio: config.noise_filter_compressor_ratio,
		comp_attack: config.noise_filter_compressor_attack_ms,
		comp_release: config.noise_filter_compressor_release_ms,
		comp_out: config.noise_filter_compressor_output_gain_db,
		lim: config.noise_filter_limiter,
		lim_ceil: config.noise_filter_limiter_ceiling_db,
		lim_rel: config.noise_filter_limiter_release_ms,
		notch: config.noise_filter_notch,
		notch_freq: config.noise_filter_notch_frequency_hz,
	});
}

interface TestResultQuality {
	volume_level: "good" | "low" | "very_low";
	volume_rms: number;
	peak_level: number;
	noise_level: "low" | "moderate" | "high";
	has_voice: boolean;
	has_clipping: boolean;
	detected_issues: string[];
	estimated_transcription_quality: number;
	silence_ratio: number;
}

interface TestStopResult {
	success: boolean;
	audio_base64: string;
	raw_audio_base64: string;
	duration_ms: number;
	sample_rate: number;
	message: string;
	quality: TestResultQuality;
}

export default function MicrophonePage() {
	const { call } = usePython();
	const [microphones, setMicrophones] =
		useState<MicrophoneDevice[]>(_cachedMicrophones);
	const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig);
	// F4 (b-review Finding 11): "Last updated" indicator state. The
	// module-level caches (`_cachedMicrophones`, `_cachedConfig`)
	// survive page navigations, so we mark the timestamp after each
	// successful loadData() to surface staleness to the user.
	const { agoLabel, markUpdated } = useLastUpdated();
	const [refreshing, setRefreshing] = useState(false);
	const [loading, setLoading] = useState(true);
	// NF-R10-2: surface backend-load failures to the user instead of
	// silently masking them. The previous implementation only logged to
	// console, leaving the user with an empty mic list and no indication
	// that the backend was unreachable (vs. genuinely no microphones).
	const [loadError, setLoadError] = useState<string | null>(null);
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
	// PVT-037 (Fix 4): initialize micMonitoring to `true` so the level
	// polling loop in the mount effect actually fires its first
	// `microphone_test_get_level` call. Previously this started at
	// `false`, and since the only thing that flips it to `true` is the
	// polling loop seeing `active: true` in the response — which never
	// happened because the loop never ran — the page deadlocked with a
	// frozen "Monitoring…" indicator and zero level bar. The mount
	// effect calls `level_monitor_start` unconditionally, so assuming
	// monitoring is active until the backend tells us otherwise is
	// correct.
	const [micMonitoring, setMicMonitoring] = useState(true);
	// Fix 15: user-configurable test recording duration (3–30s). The
	// prior implementation hard-coded `duration: 10` in the
	// `microphone_test_start` call, which was invisible to the user
	// and not adjustable for slow readers / different test phrases.
	const [testDurationSec, setTestDurationSec] = useState(10);
	// PVT-036 (Fix 3): OS-level microphone permission state. Probed
	// via `navigator.permissions.query({name: "microphone"})` on
	// mount. When "denied", we render a destructive banner with
	// platform-specific guidance + a deep-link button to the OS
	// privacy settings.
	const [micPermission, setMicPermission] = useState<
		"granted" | "denied" | "prompt" | "unknown"
	>("unknown");

	// ADR 0007: Audio preset + filter state lives in `config` directly.
	// No local duplicate — the AudioPresetSelector reads from / writes
	// to `config` via updateConfig().
	const [showAdvanced, setShowAdvanced] = useState(false);

	// Tracks whether filters have changed since last test (invalidation)
	const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>("");
	const { showSnack } = useSnackbar();
	const levelIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const audioRef = useRef<HTMLAudioElement | null>(null);
	const [playingEnhanced, setPlayingEnhanced] = useState(false);
	const [playingOriginal, setPlayingOriginal] = useState(false);
	const playingRef = useRef(false);
	const stopTestRef = useRef<() => Promise<void>>(async () => {});
	const stoppingRef = useRef(false);
	// PVT-035 (Fix 2): ref-to-latest-selectMicrophone so the
	// `microphones_changed` event handler (subscribed before
	// `selectMicrophone` is defined in the component body) can invoke
	// the latest closure without re-subscribing on every render.
	const selectMicrophoneRef = useRef<(micId: string | null) => Promise<void>>(
		async () => {},
	);

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

	// PVT-036 (Fix 3): probe the OS-level microphone permission state on
	// mount. `navigator.permissions.query({name: "microphone"})` is the
	// standard Chromium API; it works in Electron's renderer and in
	// Tauri's WebView2 (Windows) / WKWebView (macOS) when the host
	// exposes it. On Linux Tauri (WebKitGTK) it typically rejects; we
	// catch and treat the result as "unknown" (no banner shown —
	// better to be silent than to show a false-positive "permission
	// denied" banner).
	useEffect(() => {
		let cancelled = false;
		const probe = async () => {
			try {
				// Some TypeScript DOM lib versions don't include
				// "microphone" in the PermissionName union. Cast to
				// the wider string type so the call compiles without
				// mutating the global lib typings.
				const name = "microphone" as PermissionName;
				const status = await navigator.permissions.query({ name });
				if (cancelled) return;
				const state = status.state as "granted" | "denied" | "prompt";
				setMicPermission(state);
				// Listen for changes (e.g. user grants permission from
				// the OS settings dialog while the app is open).
				status.onchange = () => {
					if (cancelled) return;
					setMicPermission(
						(status.state as "granted" | "denied" | "prompt") ?? "unknown",
					);
				};
			} catch {
				if (cancelled) return;
				setMicPermission("unknown");
			}
		};
		void probe();
		return () => {
			cancelled = true;
		};
	}, []);

	// F4: manual refresh handler for the LastUpdatedIndicator button.
	// Wraps `loadData()` so we can flip a `refreshing` flag for the
	// button's spinner state without disturbing `loading` (which gates
	// the page's main spinner on first visit).
	const handleManualRefresh = useCallback(async () => {
		setRefreshing(true);
		try {
			await loadData();
		} finally {
			setRefreshing(false);
		}
	}, [loadData]);

	// CR-57: gate the 100ms polling on visibility + active state.
	const testRunningRef = useRef(false);
	const micMonitoringRef = useRef(false);
	useEffect(() => {
		testRunningRef.current = testRunning;
	}, [testRunning]);
	useEffect(() => {
		micMonitoringRef.current = micMonitoring;
	}, [micMonitoring]);

	useEffect(() => {
		const micId = config?.microphone ?? null;
		call<{ success: boolean }>("level_monitor_start", { mic_id: micId }).catch(
			(err) =>
				console.warn(
					"[IPC] microphone command failed: level_monitor_start:",
					err,
				),
		);

		levelIntervalRef.current = setInterval(async () => {
			if (
				typeof document !== "undefined" &&
				document.visibilityState !== "visible"
			)
				return;
			if (!testRunningRef.current && !micMonitoringRef.current) return;
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
			} catch {
				// Ignore polling errors
			}
		}, 100);

		const handleVisibility = () => {
			// no-op — next interval tick reads visibilityState.
		};
		if (typeof document !== "undefined") {
			document.addEventListener("visibilitychange", handleVisibility);
		}

		return () => {
			if (levelIntervalRef.current) {
				clearInterval(levelIntervalRef.current);
				levelIntervalRef.current = null;
			}
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

	// F11-FIX (b-review Finding 11): invalidate the module-level caches
	// when the backend reports that the underlying data changed through a
	// path OUTSIDE this page. The server already emits `microphones_changed`
	// (startup_tasks.py) when the device list changes; without subscribing
	// here, `_cachedMicrophones` would show stale devices until the next
	// manual refresh. `config_changed` (emitted by set_config / onboarding)
	// keeps `_cachedConfig` fresh too. Both re-run loadData() so the cache
	// AND the visible UI update — the "Last updated" indicator is only the
	// safety net, not the sole invalidation path.
	//
	// PVT-035 (Fix 2): hot-swap detection. After loadData() refreshes the
	// microphone list, if the currently-selected microphone (config.microphone)
	// is no longer present in the new list, we:
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
				// `_cachedMicrophones` is updated synchronously by loadData()
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
		}, [loadData, showSnack]),
	);

	usePythonEvent(
		"config_changed",
		useCallback((): (() => void) | undefined => {
			void loadData();
			return undefined;
		}, [loadData]),
	);

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
			// PVT-045 (session 2): pause any playing test audio to prevent
			// background playback after navigation. Also clears the audioRef
			// so onended/onerror don't fire setState on an unmounted component.
			if (audioRef.current) {
				try {
					audioRef.current.pause();
				} catch {
					/* noop */
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

	// ── Derived state ─────────────────────────────────────────────

	const activeMicId = config?.microphone ?? null;
	const isSystemDefault = activeMicId === null;
	const activeMicName =
		activeMicId === null
			? t("microphone.systemDefault")
			: (microphones.find((m) => (m.id ?? String(m.index)) === activeMicId)
					?.name ?? t("microphone.unknown"));
	const otherMicrophones = microphones
		.filter((mic) => (mic.id ?? String(mic.index)) !== activeMicId)
		.sort((a, b) => (a.default ? -1 : b.default ? 1 : 0));

	const filtersChangedSinceTest =
		filtersSinceLastTest && filtersSinceLastTest !== computeAudioKey(config);
	const hasFiltersEnabled = (config?.audio_preset ?? "auto") !== "off";

	// ── Handlers ──────────────────────────────────────────────────

	const selectMicrophone = async (micId: string | null) => {
		// Stop any active test first
		if (testRunning && !stoppingRef.current) {
			try {
				await call("microphone_test_cancel");
			} catch {
				/* ignore */
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

	stopTestRef.current = stopTest;
	// PVT-035 (Fix 2): keep selectMicrophoneRef in sync so the
	// `microphones_changed` handler (subscribed earlier in the body)
	// can invoke the latest closure without re-subscribing on every
	// render. This mirrors the stopTestRef pattern above.
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

	// ── Render ────────────────────────────────────────────────────

	if (!_cachedMicrophones.length && !_cachedConfig && loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner />
			</div>
		);
	}

	// NF-R10-2: distinguish "backend failed to load" from "no microphones
	// found" so the user knows to retry instead of being told to connect
	// a microphone when the real issue is the backend is unreachable.
	if (loadError && microphones.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
				<PageHeading
					title={t("microphone.microphone")}
					description={t("microphone.description")}
				/>
				<EmptyState
					icon={AlertCircleIcon}
					title={t("microphone.loadFailedTitle")}
					description={loadError}
					actionLabel={t("microphone.retry")}
					onAction={() => loadData()}
				/>
			</div>
		);
	}

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
			<PageHeading
				title={t("microphone.microphone")}
				description={t("microphone.description")}
			/>

			{/* F4 (b-review Finding 11): "Last updated" indicator + manual
                            refresh button. The module-level caches survive page
                            navigations, so we surface staleness here. */}
			<div className="flex justify-end pb-2">
				<LastUpdatedIndicator
					agoLabel={agoLabel}
					onRefresh={handleManualRefresh}
					refreshing={refreshing}
				/>
			</div>

			<div className="space-y-6">
				{/* PVT-036 (Fix 3): OS-level microphone permission
                                    banner. Shown only when the renderer can prove
                                    the OS has denied access (status.state === "denied").
                                    "prompt" / "unknown" do not render the banner —
                                    "prompt" is the user's first-run chance to grant,
                                    "unknown" means the API is unavailable (e.g. Linux
                                    WebKitGTK) and a false-positive banner would be
                                    worse than silence. */}
				{micPermission === "denied" && (
					<div
						role="alert"
						className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 space-y-2"
					>
						<div className="flex items-start gap-2">
							<HugeiconsIcon
								icon={AlertCircleIcon}
								strokeWidth={1.625}
								className="h-4 w-4 shrink-0 mt-0.5 text-destructive"
							/>
							<div className="flex-1 space-y-1">
								<p className="text-sm font-semibold text-destructive">
									{t("microphone.permissionDeniedTitle")}
								</p>
								<p className="text-xs text-(--text-primary)">
									{(() => {
										const ua =
											typeof navigator !== "undefined"
												? navigator.userAgent.toLowerCase()
												: "";
										if (ua.includes("mac"))
											return t("microphone.permissionDeniedMessageMacos");
										if (ua.includes("win"))
											return t("microphone.permissionDeniedMessageWindows");
										if (ua.includes("linux"))
											return t("microphone.permissionDeniedMessageLinux");
										return t("microphone.permissionDeniedMessage");
									})()}
								</p>
							</div>
						</div>
						{(() => {
							const ua =
								typeof navigator !== "undefined"
									? navigator.userAgent.toLowerCase()
									: "";
							// macOS and Windows expose a deep-link URL scheme
							// to the OS privacy settings. Linux has no
							// equivalent standard, so we omit the button.
							if (ua.includes("mac")) {
								return (
									<a
										href="x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
										aria-label={t("microphone.openSettingsAria")}
										className="inline-flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
									>
										<HugeiconsIcon
											icon={Settings03Icon}
											strokeWidth={1.625}
											className="h-3.5 w-3.5"
										/>
										{t("microphone.openSettings")}
									</a>
								);
							}
							if (ua.includes("win")) {
								return (
									<a
										href="ms-settings:privacy-microphone"
										aria-label={t("microphone.openSettingsAria")}
										className="inline-flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
									>
										<HugeiconsIcon
											icon={Settings03Icon}
											strokeWidth={1.625}
											className="h-3.5 w-3.5"
										/>
										{t("microphone.openSettings")}
									</a>
								);
							}
							return null;
						})()}
					</div>
				)}

				{/* Active Microphone Card */}
				<div
					className={cn(
						"rounded-xl border p-5 transition-colors",
						"border-accent bg-(--bg-subtle)",
					)}
				>
					{/* Mic header */}
					<div className="flex items-center justify-between">
						<div className="flex items-center gap-3">
							<HugeiconsIcon
								icon={Mic02Icon}
								strokeWidth={1.625}
								className="h-4 w-4"
							/>
							<div>
								<p className="text-sm font-semibold text-(--text-primary)">
									{activeMicName}
								</p>
								<p className="text-xs text-(--text-muted)">
									{isSystemDefault
										? t("microphone.systemDefaultDesc")
										: t("microphone.selectedMicDesc")}
								</p>
							</div>
						</div>
						<span className="inline-flex items-center rounded-md px-2.5 py-0.5 text-xs font-semibold border border-primary/20 bg-primary/10 text-primary">
							{testRunning
								? t("microphone.recordingStatus")
								: t("microphone.selected")}
						</span>
					</div>

					{/* Level bar */}
					<div className="mt-3">
						<LevelBar level={level} playing={playingRef.current} />
					</div>

					{/* Live quality feedback during test */}
					<LiveQualityFeedback
						level={level}
						peak={peak}
						isRecording={testRunning}
						elapsedSeconds={testElapsed}
						totalSeconds={testDurationSec}
					/>

					{/* Test controls */}
					<div className="mt-4 flex items-center gap-3">
						{!testRunning ? (
							<Button
								variant="default"
								size="sm"
								className="gap-2"
								disabled={playingEnhanced || playingOriginal}
								onClick={startTest}
							>
								<HugeiconsIcon
									icon={PlayIcon}
									strokeWidth={1.625}
									className="h-4 w-4"
								/>
								{t("microphone.startTest")}
							</Button>
						) : (
							<Button
								variant="default"
								size="sm"
								className="gap-2 animate-pulse"
								onClick={stopTest}
							>
								<HugeiconsIcon
									icon={StopIcon}
									strokeWidth={1.625}
									className="h-4 w-4"
								/>
								{t("microphone.stopTest", { seconds: String(testCountdown) })}
							</Button>
						)}

						{/* NF-R15-2 (a11y): split the live level indicator from
                                                    the post-test duration readout. The live level
                                                    (rapidly fluctuating during recording) is NOT
                                                    announced to avoid screen-reader spam; the post-test
                                                    duration (a single, stable value) IS announced via
                                                    aria-live="polite" so users with AT know when a test
                                                    completes and how long it ran. */}
						<span
							className="text-xs text-(--text-muted) ml-auto"
							aria-hidden={testRunning ? undefined : true}
						>
							{testRunning
								? t("microphone.level", {
										percent: String(Math.round(level * 100)),
									})
								: micMonitoring
									? t("microphone.level", {
											percent: String(Math.round(level * 100)),
										})
									: t("microphone.monitoring")}
						</span>
						{!testRunning && testDurationMs > 0 && (
							<span
								className="text-xs text-(--text-muted) ml-auto"
								aria-live="polite"
								aria-atomic="true"
							>
								{t("microphone.duration", {
									seconds: (testDurationMs / 1000).toFixed(1),
								})}
							</span>
						)}
					</div>

					{/* Fix 15: test duration slider (3–30s). The
                                            `deferApply` prop batches the drag into a
                                            single `set_config` call on pointer-up so we
                                            don't flood the backend while sliding. Hidden
                                            during an active test to avoid mid-test
                                            duration changes (which the running test
                                            ignores anyway). */}
					{!testRunning && (
						<div className="mt-3 flex items-center gap-3">
							<label
								htmlFor="mic-test-duration"
								className="text-xs font-medium text-(--text-muted) shrink-0"
							>
								{t("microphone.testDuration")}
							</label>
							<RangeSlider
								value={testDurationSec}
								min={3}
								max={30}
								step={1}
								onChange={setTestDurationSec}
								ariaLabel={t("microphone.testDurationAria")}
								suffix="s"
								deferApply
							/>
						</div>
					)}

					{/* Filter invalidation notice */}
					{filtersSinceLastTest && filtersChangedSinceTest && !testRunning && (
						<div className="mt-3 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-700 dark:text-amber-500">
							{t("microphone.filtersChangedNotice")}
						</div>
					)}

					{/* Test Review Panel */}
					<TestReviewPanel
						durationMs={testDurationMs}
						quality={testQuality}
						testAudioBase64={testAudioBase64}
						rawAudioBase64={rawAudioBase64}
						playing={playingEnhanced || playingOriginal}
						playingOriginal={playingOriginal}
						onPlayEnhanced={() =>
							testAudioBase64 && playAudio(testAudioBase64, true)
						}
						onPlayOriginal={() =>
							rawAudioBase64 ? playAudio(rawAudioBase64, false) : undefined
						}
						onStop={stopPlayback}
						onRetest={startTest}
						hasFiltersEnabled={hasFiltersEnabled}
					/>

					{/* Audio Enhancement / Preset selector */}
					<div className="mt-3">
						{config && (
							<AudioPresetSelector
								preset={(config.audio_preset as AudioPreset) ?? "auto"}
								config={config}
								showAdvanced={showAdvanced}
								onPresetChange={handlePresetChange}
								onToggleAdvanced={() => setShowAdvanced((v) => !v)}
								onConfigChange={handleConfigChange}
							/>
						)}
					</div>
				</div>

				{/* Available Microphones List */}
				{microphones.length === 0 ? (
					<EmptyState
						icon={MicOff01Icon}
						title={t("microphone.noMicrophonesFound")}
						description={t("microphone.connectAndRestart")}
					/>
				) : (
					<div>
						<p className="text-xs font-semibold capitalize tracking-wide text-(--text-muted) mb-2 px-1">
							{t("microphone.otherMicrophones")}
						</p>
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
							{/* PVT-034 (Fix 1): "Use System Default" button —
                                                            the only way (other than refreshing and hoping)
                                                            to revert from a named microphone back to the OS
                                                            default. Disabled while a test is running so the
                                                            user can't swap mics mid-recording. */}
							<div
								className={cn(
									"flex items-center gap-3 px-3.5 py-2.5",
									testRunning && "opacity-50 pointer-events-none",
								)}
							>
								<HugeiconsIcon
									icon={Mic02Icon}
									strokeWidth={2}
									className="h-4 w-4 shrink-0 text-(--text-muted)"
								/>
								<div className="flex flex-col flex-1 min-w-0 gap-1">
									<p className="text-sm font-medium text-(--text-primary)">
										{t("microphone.systemDefault")}
									</p>
									<p className="text-xs text-(--text-muted)">
										{t("microphone.systemDefaultDesc")}
									</p>
								</div>
								<Button
									variant={isSystemDefault ? "default" : "outline"}
									size="sm"
									className="shrink-0"
									disabled={isSystemDefault || testRunning}
									aria-label={t("microphone.useSystemDefaultAria")}
									onClick={() => void selectMicrophone(null)}
								>
									{t("microphone.use")}
								</Button>
							</div>
							{otherMicrophones.length === 0 ? (
								<div className="px-3.5 py-3 text-xs text-(--text-muted)">
									{t("microphone.noOtherMicrophones")}
								</div>
							) : (
								otherMicrophones.map((mic) => (
									<div
										key={mic.id ?? String(mic.index)}
										className={cn(
											testRunning && "opacity-50 pointer-events-none",
										)}
									>
										<MicrophoneListItem
											mic={mic}
											isSystemDefault={isSystemDefault}
											onSelect={(micId) => selectMicrophone(micId)}
										/>
									</div>
								))
							)}
						</div>
					</div>
				)}
			</div>
		</div>
	);
}
