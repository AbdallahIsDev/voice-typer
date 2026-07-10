import {
	Mic02Icon,
	MicOff01Icon,
	PlayIcon,
	StopIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
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
	const [loading, setLoading] = useState(true);
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
	const [micMonitoring, setMicMonitoring] = useState(false);

	// ADR 0007: Audio preset + filter state lives in `config` directly.
	// No local duplicate — the AudioPresetSelector reads from / writes
	// to `config` via updateConfig().
	const [showAdvanced, setShowAdvanced] = useState(false);

	// Tracks whether filters have changed since last test (invalidation)
	const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>("");
	const { showSnack, Snackbar } = useSnackbar();
	const levelIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
	const audioRef = useRef<HTMLAudioElement | null>(null);
	const [playingEnhanced, setPlayingEnhanced] = useState(false);
	const [playingOriginal, setPlayingOriginal] = useState(false);
	const playingRef = useRef(false);
	const stopTestRef = useRef<() => Promise<void>>(async () => {});
	const stoppingRef = useRef(false);

	/** Optimistic config update: writes through to backend + local cache. */
	const updateConfig = useCallback(
		(updates: Partial<VoiceTyperConfig>) => {
			setConfig((prev) => {
				if (!prev) return prev;
				const next = { ...prev, ...updates };
				_cachedConfig = next;
				return next;
			});
			call("set_config", updates).catch(() => {});
		},
		[call],
	);

	const loadData = useCallback(async () => {
		setLoading(true);
		try {
			const [mics, cfg] = await Promise.all([
				call<MicrophoneDevice[]>("get_microphones"),
				call<VoiceTyperConfig>("get_config"),
			]);
			_cachedMicrophones = Array.isArray(mics) ? mics : [];
			_cachedConfig = cfg;
			setMicrophones(_cachedMicrophones);
			setConfig(cfg);
		} catch (err) {
			console.error("Failed to load microphone data:", err);
		} finally {
			setLoading(false);
		}
	}, [call]);

	useEffect(() => {
		loadData();
	}, [loadData]);

	// Start continuous level monitoring on mount, stop on unmount
	useEffect(() => {
		const micId = config?.microphone ?? null;
		call<{ success: boolean }>("level_monitor_start", { mic_id: micId }).catch(
			() => {},
		);

		levelIntervalRef.current = setInterval(async () => {
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

		return () => {
			if (levelIntervalRef.current) {
				clearInterval(levelIntervalRef.current);
				levelIntervalRef.current = null;
			}
			call("level_monitor_stop").catch(() => {});
		};
	}, [call, config?.microphone]);

	usePythonEvent(
		"microphone_test_complete",
		useCallback(
			(_data: unknown) => {
				if (testRunning && !stoppingRef.current) {
					stopTestRef.current();
				}
			},
			[testRunning],
		),
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
			if (testRunning && !stoppingRef.current) {
				call("microphone_test_cancel").catch(() => {});
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
			call("level_monitor_start", { mic_id: micId }).catch(() => {});
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
				duration: 10,
				filters: buildTestFilters(config),
			});

			if (!result?.success) {
				showSnack(result?.message ?? t("microphone.startTestFailed"), "error");
				return;
			}

			setTestRunning(true);
			setTestCountdown(Math.ceil(result.duration || 10));

			// Timer countdown
			if (testTimerRef.current) clearInterval(testTimerRef.current);
			const startTime = Date.now();
			const totalDurationMs = (result.duration || 10) * 1000;
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

	return (
		<div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
			<PageHeading
				title={t("microphone.microphone")}
				description={t("microphone.description")}
			/>

			<div className="space-y-6">
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
						<span className="inline-flex items-center rounded-md px-2.5 py-0.5 text-[10px] font-semibold border border-primary/20 bg-primary/10 text-primary">
							{testRunning
								? t("microphone.recordingStatus")
								: t("microphone.active")}
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
						totalSeconds={10}
					/>

					{/* Test controls */}
					<div className="mt-4 flex items-center gap-3">
						{!testRunning ? (
							<Button
								variant="default"
								size="sm"
								className="gap-2"
								disabled={playingRef.current}
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

						<span className="text-xs text-(--text-muted) ml-auto">
							{testRunning
								? t("microphone.level", {
										percent: String(Math.round(level * 100)),
									})
								: testDurationMs > 0
									? t("microphone.duration", {
											seconds: (testDurationMs / 1000).toFixed(1),
										})
									: micMonitoring
										? t("microphone.level", {
												percent: String(Math.round(level * 100)),
											})
										: t("microphone.monitoring")}
						</span>
					</div>

					{/* Filter invalidation notice */}
					{filtersSinceLastTest && filtersChangedSinceTest && !testRunning && (
						<div className="mt-3 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-500">
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
					<div className="flex flex-col items-center justify-center py-16 gap-3">
						<HugeiconsIcon
							icon={MicOff01Icon}
							strokeWidth={1.625}
							className="h-10 w-10 text-(--text-muted) opacity-30"
						/>
						<p className="text-sm text-(--text-muted)">
							{t("microphone.noMicrophonesFound")}
						</p>
						<p className="text-xs text-(--text-muted) opacity-70">
							{t("microphone.connectAndRestart")}
						</p>
					</div>
				) : (
					<div>
						<p className="text-xs font-semibold capitalize tracking-wide text-(--text-muted) mb-2 px-1">
							{t("microphone.otherMicrophones")}
						</p>
						<div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
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

			<Snackbar />
		</div>
	);
}
