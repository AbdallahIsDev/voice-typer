// Microphone page — thin composition root.
//
// Formerly a 1193-line monolith (EC-12). Split into a
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
import { useRef } from "react";
import { LastUpdatedIndicator } from "@/components/common/LastUpdatedIndicator";
import PageHeading from "@/components/common/PageHeading";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Spinner } from "@/components/feedback/Spinner";
import { t } from "@/i18n/i18n";
import { ActiveMicrophoneCard } from "./microphone/components/ActiveMicrophoneCard";
import { AvailableMicrophonesList } from "./microphone/components/AvailableMicrophonesList";
import { MicrophonePermissionBanner } from "./microphone/components/MicrophonePermissionBanner";
import { useMicrophoneData } from "./microphone/hooks/useMicrophoneData";
import { useMicrophonePermission } from "./microphone/hooks/useMicrophonePermission";
import { useMicrophoneTest } from "./microphone/hooks/useMicrophoneTest";
import { computeAudioKey } from "./microphone/lib/computeAudioKey";

export default function MicrophonePage() {
	// PVT-035 (Fix 2): ref-to-latest-selectMicrophone so the
	// ``microphones_changed`` event handler (subscribed inside
	// ``useMicrophoneData``) can invoke the latest closure (assigned
	// inside ``useMicrophoneTest``) without re-subscribing on every
	// render.
	const selectMicrophoneRef = useRef<(micId: string | null) => Promise<void>>(
		async () => {},
	);

	const {
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
	} = useMicrophoneData({ selectMicrophoneRef });

	const { micPermission } = useMicrophonePermission();

	const {
		testRunning,
		testCountdown,
		testElapsed,
		testAudioBase64,
		rawAudioBase64,
		testDurationMs,
		testQuality,
		level,
		peak,
		micMonitoring,
		testDurationSec,
		showAdvanced,
		filtersSinceLastTest,
		playingEnhanced,
		playingOriginal,
		startTest,
		stopTest,
		selectMicrophone,
		playAudio,
		stopPlayback,
		handlePresetChange,
		handleConfigChange,
		setTestDurationSec,
		setShowAdvanced,
	} = useMicrophoneTest({
		config,
		microphones,
		setConfig,
		updateConfig,
		selectMicrophoneRef,
	});

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
				<MicrophonePermissionBanner micPermission={micPermission} />

				<ActiveMicrophoneCard
					activeMicName={activeMicName}
					isSystemDefault={isSystemDefault}
					testRunning={testRunning}
					testCountdown={testCountdown}
					testElapsed={testElapsed}
					testDurationSec={testDurationSec}
					testDurationMs={testDurationMs}
					level={level}
					peak={peak}
					micMonitoring={micMonitoring}
					testAudioBase64={testAudioBase64}
					rawAudioBase64={rawAudioBase64}
					testQuality={testQuality}
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
					onSetTestDurationSec={setTestDurationSec}
					onToggleAdvanced={() => setShowAdvanced((v) => !v)}
					onPresetChange={handlePresetChange}
					onConfigChange={handleConfigChange}
				/>

				<AvailableMicrophonesList
					microphones={microphones}
					otherMicrophones={otherMicrophones}
					isSystemDefault={isSystemDefault}
					testRunning={testRunning}
					onSelectMicrophone={selectMicrophone}
				/>
			</div>
		</div>
	);
}
