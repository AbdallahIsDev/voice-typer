// Microphone page — thin composition root.
//
//Formerly a 1193-line monolith (). Split into a
// ``pages/microphone/`` package: this file wires the three hooks
// (``useMicrophoneData`` / ``useMicrophonePermission`` /
// ``useMicrophoneTest``) and renders the three sub-components
// (``MicrophonePermissionBanner`` / ``ActiveMicrophoneCard`` /
// ``AvailableMicrophonesList``). All business logic lives in the
// hooks; all layout lives in the components.
//
// The default export signature is preserved so ``App.tsx`` routing is
// unaffected.

import { AlertCircleIcon } from "@hugeicons/core-free-icons";
import { useCallback, useRef, useState } from "react";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { OfflinePackPreparingBanner } from "@/components/feedback/OfflinePackPreparingBanner";
import { Spinner } from "@/components/feedback/Spinner";
import { useOfflinePackDownload } from "@/hooks/useOfflinePackDownload";
import { t } from "@/i18n/i18n";
import { VOICE_BIOMETRIC_CONSENT_FIELD } from "@/lib/consent";
import { consentBodyKey, openConsentGate } from "@/lib/consentGate";
import { useDeviceLostStore } from "@/stores/deviceLostStore";
import { ActiveMicrophoneCard } from "./microphone/components/ActiveMicrophoneCard";
import { AvailableMicrophonesList } from "./microphone/components/AvailableMicrophonesList";
import { MicrophoneDeviceLostBanner } from "./microphone/components/MicrophoneDeviceLostBanner";
import { MicrophonePermissionBanner } from "./microphone/components/MicrophonePermissionBanner";
import { useMicrophoneData } from "./microphone/hooks/useMicrophoneData";
import { useMicrophonePermission } from "./microphone/hooks/useMicrophonePermission";
import { useMicrophoneTest } from "./microphone/hooks/useMicrophoneTest";
import { computeAudioKey } from "./microphone/lib/computeAudioKey";

export default function MicrophonePage() {
	// ref-to-latest-selectMicrophone so the
	// ``microphones_changed`` event handler (subscribed inside
	// ``useMicrophoneData``) can invoke the latest closure (assigned
	// inside ``useMicrophoneTest``) without re-subscribing on every
	// render.
	const selectMicrophoneRef = useRef<(micId: string | null) => Promise<void>>(
		async () => {},
	);
	// meter wrapper ref. The level monitor's rAF loop imperatively
	// writes the latest level to the ``LevelBar``'s fill div inside this
	// wrapper, bypassing React's re-render cycle (mirrors the bubble's
	// ``useAudioLevels`` ref+rAF pattern). Attached to a ``<div>`` wrapping
	// ``<ActiveMicrophoneCard>`` so ``querySelector('[role="progressbar"]
	// > div')`` can reach the ``LevelBar``'s fill element without modifying
	// ``ActiveMicrophoneCard``.
	const meterRef = useRef<HTMLDivElement | null>(null);

	const {
		microphones,
		config,
		setConfig,
		loading,
		loadError,
		loadData,
		updateConfig,
	} = useMicrophoneData({ selectMicrophoneRef });

	const { micPermission } = useMicrophonePermission();

	// Device-lost state — written ONCE by the App-level
	// ``useDeviceLostToast`` subscriber (single event → single
	// mechanism). While a loss is flagged the level monitor is paused
	// (futile to monitor a vanished stream) and the recovery banner
	// below replaces the dead meter with a Retry affordance.
	const lostSource = useDeviceLostStore((s) => s.lostSource);
	const clearLost = useDeviceLostStore((s) => s.clearLost);

	// Recovery affordance: clear the lost flag + refresh the device
	// list. Flipping the flag false un-pauses the level monitor (its
	// effect re-runs and restarts monitoring); if the mic is still gone
	// the backend re-emits ``device_lost`` and the banner returns.
	const handleDeviceRecovery = useCallback(() => {
		clearLost();
		void loadData();
	}, [clearLost, loadData]);

	// Runtime-pack readiness — drives the "Preparing offline engine…"
	// banner below. The mic test itself uses RMS only and works without
	// the pack (§4.9: "RMS meter works; VAD 'smartness' degrades
	// silently"), so the banner is purely informational — it does NOT
	// block the test.
	const { status: packStatus, isReady: packReady } = useOfflinePackDownload();
	// Tracks whether the user has attempted an action that would
	// normally require the pack (startTest or selectMicrophone). The
	// banner is gated on this so a fresh page load with a missing pack
	// doesn't surface the "Preparing…" line until the user actually
	// interacts with the mic-test surface.
	const [hasAttempted, setHasAttempted] = useState(false);

	const {
		testRunning,
		testCountdown,
		testElapsed,
		testAudioBase64,
		rawAudioBase64,
		testDurationMs,
		testQuality,
		testTranscription,
		testTranscriptionUnavailable,
		level,
		peak,
		micMonitoring,
		showAdvanced,
		filtersSinceLastTest,
		playingEnhanced,
		playingOriginal,
		startTest: rawStartTest,
		stopTest,
		selectMicrophone: rawSelectMicrophone,
		playAudio,
		stopPlayback,
		handlePresetChange,
		handleConfigChange,
		setShowAdvanced,
	} = useMicrophoneTest({
		config,
		microphones,
		setConfig,
		updateConfig,
		selectMicrophoneRef,
		meterRef,
		levelMonitorPaused: lostSource !== null,
	});

	// GDPR Art. 9 gate — mirrors Home.tsx handleToggle: the backend
	// refuses mic-test starts while ``voice_biometric_consent`` is off,
	// but its refusal only surfaces as a post-hoc error path. Gate
	// client-side at ACTION time so pressing Start Test opens the unified
	// point-of-use consent dialog INSTEAD of firing a doomed IPC; once the
	// consent flag is true the test starts directly and never re-prompts.
	// The session hook's reactive refusal paths remain as the backstop for
	// hotkey-triggered tests and config races.
	const startTest = useCallback(
		(...args: Parameters<typeof rawStartTest>) => {
			setHasAttempted(true);
			if (config && !config.voice_biometric_consent) {
				openConsentGate({
					consentField: VOICE_BIOMETRIC_CONSENT_FIELD,
					bodyKey: consentBodyKey(VOICE_BIOMETRIC_CONSENT_FIELD),
					// Retry after granting: run the test start that was blocked.
					onAllow: () => void rawStartTest(...args),
				});
				return;
			}
			return rawStartTest(...args);
		},
		[rawStartTest, config],
	);

	// Wrap selectMicrophone so the first invocation flips `hasAttempted`
	// to true. Selecting a microphone WHILE a device-lost flag is active
	// clears that flag first — otherwise the stale banner + frozen meter
	// linger over a now-working device. If the picked mic is still gone
	// the backend re-emits ``device_lost`` and the banner returns. A data
	// refresh follows so the device list reflects the switch immediately.
	const selectMicrophone = useCallback(
		async (...args: Parameters<typeof rawSelectMicrophone>) => {
			setHasAttempted(true);
			if (lostSource !== null) {
				clearLost();
			}
			try {
				return await rawSelectMicrophone(...args);
			} finally {
				if (lostSource !== null) {
					await loadData();
				}
			}
		},
		[rawSelectMicrophone, lostSource, clearLost, loadData],
	);

	// ── Derived state ─────────────────────────────────────────────

	const activeMicId = config?.microphone ?? null;
	const isSystemDefault = activeMicId === null;
	const activeMicName =
		activeMicId === null
			? t("microphone.systemDefault")
			: (microphones.find((m) => (m.id ?? String(m.index)) === activeMicId)
					?.name ?? t("microphone.unknown"));

	const filtersChangedSinceTest: string | false =
		filtersSinceLastTest && filtersSinceLastTest !== computeAudioKey(config)
			? filtersSinceLastTest
			: false;
	const hasFiltersEnabled = (config?.audio_preset ?? "auto") !== "off";

	// ── Render ────────────────────────────────────────────────────

	// On first visit (no cached data + still loading), show the full
	// spinner. On re-visit, the module-level caches in
	// ``useMicrophoneData`` populate the initial state so we render the
	// page immediately (the spinner would flash stale-then-real).
	if (!microphones.length && !config && loading) {
		return (
			<div className="flex h-full items-center justify-center">
				<Spinner label={t("microphone.loading")} />
			</div>
		);
	}

	//distinguish "backend failed to load" from "no microphones
	// found" so the user knows to retry instead of being told to connect
	// a microphone when the real issue is the backend is unreachable.
	if (loadError && microphones.length === 0) {
		return (
			<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
				<PageHeading
					title={t("microphone.microphone")}
					description={t("microphone.description")}
				/>
				{/*variant="error" so the destructive
					tint + Alert02Icon swap make the failure visually distinct
					from a genuine empty list (matches the Vocabulary/Templates
					load-failure pattern from ). */}
				<EmptyState
					variant="error"
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
		<div className="mx-auto flex min-h-full w-full max-w-4xl flex-col px-16 pt-28 pb-6">
			<PageHeading
				title={t("microphone.microphone")}
				description={t("microphone.description")}
			/>

			<div className="space-y-6">
				<MicrophonePermissionBanner micPermission={micPermission} />

				{/* Device-lost recovery — shown while the backend has
			    flagged the active microphone as lost. The level monitor
			    is paused for the same condition; Retry clears the flag +
			    refreshes the device list, which restarts monitoring.
			    Suppressed while OS mic permission is denied: a revoked
			    permission fails the backend stream too (→ device_lost),
			    and stacking two role=alert banners for one root cause
			    drowns the actionable one (grant permission). */}
				<MicrophoneDeviceLostBanner
					visible={lostSource !== null && micPermission !== "denied"}
					onRetry={handleDeviceRecovery}
				/>

				{/* Runtime-pack readiness banner — §4.8 / §4.9. Visible only
				when the pack isn't ready AND the user has actually
				started a test or picked a microphone. The mic test
				itself works without the pack (RMS only — §4.9), so
				this banner is purely informational; it does NOT block
				the Start Test button. */}
				<OfflinePackPreparingBanner
					visible={!packReady && hasAttempted}
					status={packStatus}
				/>

				{/* ``meterRef`` wrapper. The level monitor's rAF
					loop queries inside this div for the ``LevelBar``'s fill
					node (``[role="progressbar"] > div``) and imperatively
					writes the latest level/colour at ≤60 Hz — bypassing
					React's re-render cycle so a 30 Hz ``mic_level`` push
					no longer re-renders the entire Microphone page subtree.
					The wrapper itself is a no-op layout element (no extra
					className) so the visual structure is unchanged. */}
				<div ref={meterRef}>
					<ActiveMicrophoneCard
						activeMicName={activeMicName}
						isSystemDefault={isSystemDefault}
						canTest={microphones.length > 0}
						testRunning={testRunning}
						testCountdown={testCountdown}
						testElapsed={testElapsed}
						testDurationMs={testDurationMs}
						level={level}
						peak={peak}
						micMonitoring={micMonitoring}
						testAudioBase64={testAudioBase64}
						rawAudioBase64={rawAudioBase64}
						testQuality={testQuality}
						testTranscription={testTranscription}
						testTranscriptionUnavailable={testTranscriptionUnavailable}
						playing={playingEnhanced || playingOriginal}
						playingOriginal={playingOriginal}
						filtersSinceLastTest={filtersSinceLastTest}
						filtersChangedSinceTest={filtersChangedSinceTest}
						hasFiltersEnabled={hasFiltersEnabled}
						showAdvanced={showAdvanced}
						config={config}
						onStartTest={startTest}
						onStopTest={stopTest}
						onPlayEnhanced={() =>
							testAudioBase64 && playAudio(testAudioBase64, true)
						}
						onPlayOriginal={() =>
							rawAudioBase64 ? playAudio(rawAudioBase64, false) : undefined
						}
						onStopPlayback={stopPlayback}
						onRetest={startTest}
						onToggleAdvanced={() => setShowAdvanced((v) => !v)}
						onPresetChange={handlePresetChange}
						onConfigChange={handleConfigChange}
					/>
				</div>

				<AvailableMicrophonesList
					microphones={microphones}
					activeMicId={activeMicId}
					testRunning={testRunning}
					onSelectMicrophone={selectMicrophone}
				/>
			</div>
		</div>
	);
}
