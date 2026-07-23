// Active-microphone card.
//
// Renders the currently-selected microphone (or "System Default")
// header, the live LevelBar, the LiveQualityFeedback during a test,
// the start/stop test buttons, the test-duration slider, the
// "filters changed" invalidation notice, the TestReviewPanel (post-test
// quality + playback), and the AudioPresetSelector. This is the
// primary interactive surface of the Microphone page.
//
// Pure presentational component — all state and handlers are passed in
// from the page (which wires them from ``useMicrophoneData`` /
// ``useMicrophoneTest``). The card owns no business logic.

import { Mic02Icon, PlayIcon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { RangeSlider } from "@/components/common/RangeSlider";
import { LevelBar } from "@/components/feedback/LevelBar";
import { LiveQualityFeedback } from "@/components/feedback/LiveQualityFeedback";
import {
	type AudioPreset,
	AudioPresetSelector,
} from "@/components/microphone/AudioPresetSelector";
import { TestReviewPanel } from "@/components/microphone/TestReviewPanel";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type { TestResultQuality } from "../lib/types";

export interface ActiveMicrophoneCardProps {
	activeMicName: string;
	isSystemDefault: boolean;
	testRunning: boolean;
	testCountdown: number;
	testElapsed: number;
	testDurationSec: number;
	testDurationMs: number;
	level: number;
	peak: number;
	micMonitoring: boolean;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	testQuality: TestResultQuality | null;
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
	onSetTestDurationSec: (value: number) => void;
	onToggleAdvanced: () => void;
	onPresetChange: (preset: AudioPreset) => void;
	onConfigChange: (updates: Partial<VoiceTyperConfig>) => void;
}

export function ActiveMicrophoneCard({
	activeMicName,
	isSystemDefault,
	testRunning,
	testCountdown,
	testElapsed,
	testDurationSec,
	testDurationMs,
	level,
	peak,
	micMonitoring,
	testAudioBase64,
	rawAudioBase64,
	testQuality,
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
	onSetTestDurationSec,
	onToggleAdvanced,
	onPresetChange,
	onConfigChange,
}: ActiveMicrophoneCardProps) {
	return (
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
				<LevelBar level={level} playing={playing} />
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
						disabled={playing}
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
			    ``deferApply`` prop batches the drag into a single
			    ``set_config`` call on pointer-up so we don't flood the
			    backend while sliding. Hidden during an active test to
			    avoid mid-test duration changes (which the running test
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
						onChange={onSetTestDurationSec}
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
				playing={playing}
				playingOriginal={playingOriginal}
				onPlayEnhanced={onPlayEnhanced}
				onPlayOriginal={onPlayOriginal}
				onStop={onStopPlayback}
				onRetest={onRetest}
				hasFiltersEnabled={hasFiltersEnabled}
			/>

			{/* Audio Enhancement / Preset selector */}
			<div className="mt-3">
				{config && (
					<AudioPresetSelector
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
