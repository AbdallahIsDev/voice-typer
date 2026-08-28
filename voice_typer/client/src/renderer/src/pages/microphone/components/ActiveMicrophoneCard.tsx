// Active-microphone card.
//
// Renders the currently-selected microphone (or "System Default")
// header, the live LevelBar, the LiveQualityFeedback during a test,
// the start/stop test buttons, the "filters changed" invalidation
// notice, the TestReviewPanel (post-test transcription, quality +
// playback), and the PresetAccordionSelector. This is the primary
// interactive surface of the Microphone page.
//
// Pure presentational component — all state and handlers are passed in
// from the page (which wires them from ``useMicrophoneData`` /
// ``useMicrophoneTest``). The card owns no business logic.
//
// Memoised children: the heaviest subtrees —
// `PresetAccordionSelector` (renders the full `AudioFilterChain` when
// `preset === "custom"`) and `TestReviewPanel` (post-test quality
// metrics + playback controls) — are wrapped in `React.memo` with a
// custom comparator so a 10 Hz `mic_level` push (which updates
// `level`/`peak` and re-renders this card) doesn't re-render them.
// Only `LevelBarContainer` (the `LevelBar` + `LiveQualityFeedback`
// pair that actually consumes `level`/`peak`) re-renders on each push.

import { Mic02Icon, PlayIcon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { memo } from "react";
import { LevelBar } from "@/components/feedback/LevelBar";
import { LiveQualityFeedback } from "@/components/feedback/LiveQualityFeedback";
import type { AudioPreset } from "@/components/microphone/AudioPresetSelector";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import { MICROPHONE_TEST_DURATION_SEC } from "../hooks/useMicrophoneTestSession";
import type { TestResultQuality } from "../lib/types";
import { PresetAccordionSelector } from "./PresetAccordionSelector";

export interface ActiveMicrophoneCardProps {
	activeMicName: string;
	isSystemDefault: boolean;
	/** Whether the Start Test button should be enabled. False when the
	 *  backend reports zero available microphones — issuing
	 *  ``microphone_test_start`` with no mic (even the system default)
	 *  fails and spams error snacks. */
	canTest: boolean;
	testRunning: boolean;
	testElapsed: number;
	testDurationMs: number;
	level: number;
	micMonitoring: boolean;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	testQuality: TestResultQuality | null;
	/** Transcription of the last test recording (null = none/failed). */
	testTranscription?: string | null;
	/** True when no speech model is loaded, so no transcription can exist. */
	testTranscriptionUnavailable?: boolean;
	/** Whether enhanced OR original playback is in flight. */
	playing: boolean;
	playingOriginal: boolean;
	/** Snapshot of the audio key at last test start ("" = no test yet). */
	filtersSinceLastTest: string;
	/** Non-empty when filters have drifted since the last test. */
	filtersChangedSinceTest: string | false;
	hasFiltersEnabled: boolean;
	showAdvanced: boolean;
	config: VoiceTyperConfig | null;
	onStartTest: () => void;
	onStopTest: () => void;
	onPlayEnhanced: () => void;
	onPlayOriginal: () => void;
	onStopPlayback: () => void;
	onRetest: () => void;
	onToggleAdvanced: () => void;
	onPresetChange: (preset: AudioPreset) => void;
	onConfigChange: (updates: Partial<VoiceTyperConfig>) => void;
}

export function ActiveMicrophoneCard({
	activeMicName,
	isSystemDefault,
	canTest,
	testRunning,
	testElapsed,
	testDurationMs,
	level,
	micMonitoring,
	testAudioBase64,
	rawAudioBase64,
	testQuality,
	testTranscription,
	testTranscriptionUnavailable,
	playing,
	playingOriginal,
	filtersSinceLastTest,
	filtersChangedSinceTest,
	hasFiltersEnabled,
	showAdvanced,
	config,
	onStartTest,
	onStopTest,
	onPlayEnhanced,
	onPlayOriginal,
	onStopPlayback,
	onRetest,
	onToggleAdvanced,
	onPresetChange,
	onConfigChange,
}: ActiveMicrophoneCardProps) {
	return (
		<div
			className={cn(
				"rounded-xl border border-border/5 p-5 transition-colors",
				"bg-(--bg-subtle)",
			)}
		>
			{/* Mic header */}
			<div className="flex items-center gap-3 min-w-0">
				<HugeiconsIcon
					icon={Mic02Icon}
					strokeWidth={1.625}
					className="h-4 w-4 shrink-0"
				/>
				<div className="min-w-0">
					<p className="text-sm font-semibold text-(--text-primary) truncate">
						{activeMicName}
					</p>
					{/* Description only for System Default — its text carries
					information (which device the OS routes to). For a selected
					device a "Selected microphone" line is redundant with the
					radio list + card context (C-MIC-13), so no desc renders. */}
					{isSystemDefault && (
						<p className="text-xs text-(--text-muted)">
							{t("microphone.systemDefaultDesc")}
						</p>
					)}
				</div>
			</div>

			{/* : LevelBarContainer bundles the level-driven children
                            (LevelBar + LiveQualityFeedback) so the rest of the card
                            (TestReviewPanel, AudioPresetSelector, test controls) can be
                            memoised against level/peak changes. The container itself
                            re-renders on every mic_level push (it consumes `level` and
                            `peak` directly) — that's the intended behavior, since
                            LevelBar's visual height + LiveQualityFeedback's peak marker
                            both depend on the latest values. */}
			<LevelBarContainer
				level={level}
				playing={playing}
				testRunning={testRunning}
				testElapsed={testElapsed}
			/>

			{/* Test controls */}
			<div className="mt-4 flex items-center gap-3">
				{!testRunning ? (
					<Button
						variant="default"
						size="sm"
						className="gap-2"
						disabled={playing || !canTest}
						onClick={onStartTest}
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
						onClick={onStopTest}
					>
						<HugeiconsIcon
							icon={StopIcon}
							strokeWidth={1.625}
							className="h-4 w-4"
						/>
						{t("microphone.stopTest")}
					</Button>
				)}

				{/*(a11y): split the live level indicator from
                                    the post-test duration readout. The live level
                                    (rapidly fluctuating during recording) is NOT
                                    announced to avoid screen-reader spam; the post-test
                                    duration (a single, stable value) IS announced via
                                    aria-live="polite" so users with AT know when a test
                                    completes and how long it ran. Only the FIRST trailing
                                    span carries ``ml-auto`` — a second one would fight it
                                    for the free space and push the duration readout off.

                                    aria-hidden={true} is UNCONDITIONAL on the live-level
                                    span — the sibling ``LevelBar`` exposes the live value to
                                    assistive tech via its ``role="progressbar"`` +
                                    ``aria-valuenow`` attributes (the bar element is the
                                    accessible source of truth for the level). Letting
                                    this textual "Level: NN%" readout be announced too
                                    would duplicate the value AND spam AT at up to 30 Hz
                                    (the ``mic_level`` push rate). */}
				<span
					className="text-xs text-(--text-muted) ml-auto"
					aria-hidden={true}
				>
					{testRunning
						? t("microphone.level", {
								percent: String(Math.round(level * 100)),
							})
						: micMonitoring
							? t("microphone.level", {
									percent: String(Math.round(level * 100)),
								})
							: t("microphone.monitoringOff")}
				</span>
				{!testRunning && testDurationMs > 0 && (
					<span
						className="text-xs text-(--text-muted)"
						aria-live="polite"
						aria-atomic="true"
					>
						{t("microphone.duration", {
							seconds: (testDurationMs / 1000).toFixed(1),
						})}
					</span>
				)}
			</div>

			{/* Filter invalidation notice */}
			{filtersSinceLastTest && filtersChangedSinceTest && !testRunning && (
				<div className="mt-3 px-3 py-2 rounded-lg bg-warning/10 border border-warning/20 text-xs text-warning">
					{t("microphone.filtersChangedNotice")}
				</div>
			)}

			{/* : Test Review Panel — memoised. Re-renders only when
                            the post-test data (duration, quality, audio, playback
                            state) actually changes, NOT on every mic_level push.
                            The comparator ignores callback identity (the parent
                            Microphone.tsx creates fresh inline closures for
                            onPlayEnhanced / onPlayOriginal / onStop / onRetest on
                            every render — those don't affect TestReviewPanel's
                            rendered output, so skipping the re-render is safe). */}
			<MemoizedTestReviewPanel
				durationMs={testDurationMs}
				quality={testQuality}
				transcription={testTranscription}
				transcriptionUnavailable={testTranscriptionUnavailable}
				testAudioBase64={testAudioBase64}
				rawAudioBase64={rawAudioBase64}
				playing={playing}
				playingOriginal={playingOriginal}
				onPlayEnhanced={onPlayEnhanced}
				onPlayOriginal={onPlayOriginal}
				onStop={onStopPlayback}
				onRetest={onRetest}
				hasFiltersEnabled={hasFiltersEnabled}
				// Wire the preset-applier so detected-noise
				// issues render a one-click "Apply Noisy Room preset"
				// CTA. ``onPresetChange`` is useCallback-stable in
				// useMicrophoneTest, so identity churn never re-renders
				// the panel (verified by the custom memo comparator below,
				// which now includes ``onApplyPreset`` + ``currentPreset``).
				onApplyPreset={onPresetChange}
				currentPreset={
					config ? ((config.audio_preset as AudioPreset) ?? "auto") : undefined
				}
			/>

			{/* : Preset selector (accordion + radio) — memoised.
                            Re-renders only when the preset / config / showAdvanced
                            flag changes, NOT on every mic_level push. The
                            comparator includes `onConfigChange` and `onPresetChange`
                            (both useCallback-stable in `useMicrophoneTest`) but
                            excludes `onToggleAdvanced` (inline closure in
                            Microphone.tsx — identity changes per render but the
                            behavior is identical, so skipping is safe). */}
			<div className="mt-3">
				{config && (
					<MemoizedPresetAccordionSelector
						preset={(config.audio_preset as AudioPreset) ?? "auto"}
						config={config}
						showAdvanced={showAdvanced}
						onPresetChange={onPresetChange}
						onToggleAdvanced={onToggleAdvanced}
						onConfigChange={onConfigChange}
					/>
				)}
			</div>
		</div>
	);
}

// ── : LevelBarContainer ─────────────────────────────────────────
//
// Bundles the two children that consume `level` / `peak`:
// - `<LevelBar>` — the live horizontal bar.
// - `<LiveQualityFeedback>` — peak marker + test progress readout.
//
// `level` updates at 10 Hz (or ≤30 Hz once 's `mic_level` push
// lands) — this container re-renders on every push, which is the
// intended behavior. The point of the split is that the SIBLING
// subtrees (`MemoizedTestReviewPanel`, `MemoizedAudioPresetSelector`)
// are wrapped in `React.memo` and skip re-render on level-only
// changes — so a 30 Hz level push only re-renders this container +
// the two feedback children, not the entire card.

interface LevelBarContainerProps {
	level: number;
	playing: boolean;
	testRunning: boolean;
	testElapsed: number;
}

function LevelBarContainer({
	level,
	playing,
	testRunning,
	testElapsed,
}: LevelBarContainerProps) {
	return (
		<>
			{/* Level bar */}
			<div className="mt-3">
				<LevelBar level={level} playing={playing} />
			</div>

			{/* Live quality feedback during test */}
			<LiveQualityFeedback
				isRecording={testRunning}
				elapsedSeconds={testElapsed}
				totalSeconds={MICROPHONE_TEST_DURATION_SEC}
			/>
		</>
	);
}

// ── : Memoised children ─────────────────────────────────────────
//
// `React.memo` with a custom comparator. The comparator focuses on the
// props that actually affect the rendered output — callback identity
// changes (which happen on every Microphone.tsx render due to inline
// closures) are ignored so a 10–30 Hz `mic_level` push doesn't
// re-render these heavy subtrees.
//
// `onConfigChange` IS included in the AudioPresetSelector comparator
// because it's `useCallback`-stable in `useMicrophoneTest` (its
// identity changes only when `updateConfig` changes, which happens
// rarely). Including it lets us skip re-renders even when an upstream
// component forgets to memoise a wrapper. Same for `onPresetChange`.

const MemoizedPresetAccordionSelector = memo(
	PresetAccordionSelector,
	(prev, next) =>
		prev.preset === next.preset &&
		prev.config === next.config &&
		prev.showAdvanced === next.showAdvanced &&
		prev.onConfigChange === next.onConfigChange &&
		prev.onPresetChange === next.onPresetChange,
);

const MemoizedTestReviewPanel = memo(
	TestReviewPanel,
	(prev, next) =>
		prev.durationMs === next.durationMs &&
		prev.quality === next.quality &&
		prev.transcription === next.transcription &&
		prev.transcriptionUnavailable === next.transcriptionUnavailable &&
		prev.testAudioBase64 === next.testAudioBase64 &&
		prev.rawAudioBase64 === next.rawAudioBase64 &&
		prev.playing === next.playing &&
		prev.playingOriginal === next.playingOriginal &&
		prev.hasFiltersEnabled === next.hasFiltersEnabled &&
		// Include the new preset-applier + current preset in
		// the comparator. ``onApplyPreset`` is useCallback-stable
		// (delegated to ``onPresetChange`` in useMicrophoneTest) so
		// identity churn is rare; ``currentPreset`` flips when the
		// user picks a new preset, which SHOULD re-render so the
		// recommendation CTA's disabled-state updates.
		prev.onApplyPreset === next.onApplyPreset &&
		prev.currentPreset === next.currentPreset,
);
