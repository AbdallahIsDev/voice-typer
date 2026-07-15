import { PlayIcon, RefreshIcon, StopIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface QualityData {
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

interface TestReviewPanelProps {
	durationMs: number;
	quality: QualityData | null;
	testAudioBase64: string | null;
	rawAudioBase64: string | null;
	playing: boolean;
	playingOriginal: boolean;
	onPlayEnhanced: () => void;
	onPlayOriginal: () => void;
	onStop: () => void;
	onRetest: () => void;
	hasFiltersEnabled: boolean;
}

function _QualityScore({ value, max }: { value: number; max: number }) {
	const pct = (value / max) * 100;
	const color =
		pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500";
	return (
		<div className="flex items-center gap-2">
			<div className="h-1.5 w-24 rounded-full bg-border overflow-hidden">
				<div
					className={`h-full rounded-full transition-all ${color}`}
					style={{ width: `${pct}%` }}
				/>
			</div>
			<span className="text-xs tabular-nums text-(--text-muted) w-8 text-right">
				{value}/{max}
			</span>
		</div>
	);
}

export function TestReviewPanel({
	durationMs,
	quality,
	testAudioBase64,
	rawAudioBase64,
	playing,
	playingOriginal,
	onPlayEnhanced,
	onPlayOriginal,
	onStop,
	onRetest,
	hasFiltersEnabled,
}: TestReviewPanelProps) {
	if (!testAudioBase64 && !rawAudioBase64) return null;

	return (
		<div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-4 space-y-3">
			{/* Header */}
			<div className="flex items-center justify-between">
				{" "}
				<div>
					<p className="text-sm font-semibold text-(--text-primary)">
						{t("microphoneTest.title")}
					</p>
					<p className="text-xs text-(--text-muted)">
						{t("microphoneTest.duration", {
							duration: (durationMs / 1000).toFixed(1),
						})}
					</p>
				</div>
				<Button
					variant="outline"
					size="sm"
					className="gap-1.5"
					onClick={onRetest}
				>
					<HugeiconsIcon
						icon={RefreshIcon}
						strokeWidth={1.625}
						className="h-3.5 w-3.5"
					/>
					{t("microphoneTest.retest")}
				</Button>
			</div>

			{/* Quality score */}
			{quality && (
				<>
					<div className="flex items-center justify-between">
						<span className="text-xs font-medium text-(--text-muted)">
							{t("microphoneTest.estimatedQuality")}
						</span>
						<span
							className={`text-sm font-bold tabular-nums ${
								quality.estimated_transcription_quality >= 80
									? "text-green-500"
									: quality.estimated_transcription_quality >= 50
										? "text-amber-500"
										: "text-red-500"
							}`}
						>
							{quality.estimated_transcription_quality}%
						</span>
					</div>

					{/* Detailed metrics */}
					<div className="grid grid-cols-2 gap-2 text-xs">
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.volume")}
							</span>
							<div className="flex items-center gap-1.5">
								<span
									className={`w-1.5 h-1.5 rounded-full ${
										quality.volume_level === "good"
											? "bg-green-500"
											: "bg-amber-500"
									}`}
								/>
								<span>
									{quality.volume_level === "good"
										? t("microphoneTest.good")
										: quality.volume_level === "low"
											? t("microphoneTest.low")
											: t("microphoneTest.veryLow")}
								</span>
							</div>
						</div>
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.backgroundNoise")}
							</span>
							<div className="flex items-center gap-1.5">
								<span
									className={`w-1.5 h-1.5 rounded-full ${
										quality.noise_level === "low"
											? "bg-green-500"
											: quality.noise_level === "moderate"
												? "bg-amber-500"
												: "bg-red-500"
									}`}
								/>
								<span>
									{quality.noise_level === "low"
										? t("microphoneTest.lowNoise")
										: quality.noise_level === "moderate"
											? t("microphoneTest.moderateNoise")
											: t("microphoneTest.highNoise")}
								</span>
							</div>
						</div>
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.clipping")}
							</span>
							<span className="ms-1">
								{quality.has_clipping
									? t("microphoneTest.clippingDetected")
									: t("microphoneTest.clippingNone")}
							</span>
						</div>
						<div>
							<span className="text-(--text-muted)">
								{t("microphoneTest.voice")}
							</span>
							<span className="ms-1">
								{quality.has_voice
									? t("microphoneTest.voiceDetected")
									: t("microphoneTest.voiceNotDetected")}
							</span>
						</div>
					</div>

					{/* Detected issues */}
					{quality.detected_issues.length > 0 && (
						<div className="text-xs text-(--text-muted) space-y-0.5">
							<span className="font-medium text-amber-500">
								{t("microphoneTest.detectedIssues")}
							</span>
							{quality.detected_issues.map((issue) => (
								<div key={issue} className="flex items-center gap-1">
									<span className="text-amber-500">•</span>
									<span>{issue}</span>
								</div>
							))}
						</div>
					)}
				</>
			)}

			{/* Playback controls */}
			<div className="flex flex-wrap items-center gap-2">
				{testAudioBase64 && hasFiltersEnabled && (
					<Button
						variant="outline"
						size="sm"
						className="gap-1.5"
						onClick={playing && !playingOriginal ? onStop : onPlayEnhanced}
					>
						<HugeiconsIcon
							icon={playing && !playingOriginal ? StopIcon : PlayIcon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
						{playing && !playingOriginal
							? t("microphoneTest.stop")
							: t("microphoneTest.playEnhanced")}
					</Button>
				)}

				{rawAudioBase64 && (
					<Button
						variant="outline"
						size="sm"
						className="gap-1.5"
						onClick={playing && playingOriginal ? onStop : onPlayOriginal}
					>
						<HugeiconsIcon
							icon={playing && playingOriginal ? StopIcon : PlayIcon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
						{playing && playingOriginal
							? t("microphoneTest.stop")
							: t("microphoneTest.playOriginal")}
					</Button>
				)}

				{!hasFiltersEnabled && testAudioBase64 && (
					<Button
						variant="outline"
						size="sm"
						className="gap-1.5"
						onClick={playing ? onStop : onPlayEnhanced}
					>
						<HugeiconsIcon
							icon={playing ? StopIcon : PlayIcon}
							strokeWidth={1.625}
							className="h-3.5 w-3.5"
						/>
						{playing
							? t("microphoneTest.stop")
							: t("microphoneTest.playRecording")}
					</Button>
				)}
			</div>
		</div>
	);
}
