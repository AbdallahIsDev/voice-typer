import { toPng } from "html-to-image";
import { useCallback, useRef } from "react";
import type { TodayStats } from "@/types/ipc";
import type { ShareStats } from "@/types/stats";

const AVG_TYPING_WPM = 40;

/** Cloud ASR backends. */
const CLOUD_BACKENDS = new Set(["openai", "groq", "deepgram"]);

/**
 * Pure function: compute shareable stats from today's data + config.
 *
 * This is intentionally a pure function (no hooks, no side effects)
 * so it's easy to test and reuse.
 */
export function computeShareStats(
	todayStats: TodayStats,
	asrBackend: string,
): ShareStats {
	const durationMinutes = todayStats.duration / 60;
	const wpm =
		durationMinutes > 0
			? Math.round(todayStats.word_count / durationMinutes)
			: 0;

	const minutesSaved =
		durationMinutes > 0
			? Math.max(
					0,
					Math.round(todayStats.word_count / AVG_TYPING_WPM - durationMinutes),
				)
			: 0;

	const isCloud = CLOUD_BACKENDS.has(asrBackend);
	const modeDisplay = isCloud ? "Cloud" : "Offline";
	const modeDetail = isCloud ? "Cloud API" : "Local Model";

	const fasterPercent =
		wpm > 0 ? Math.round(((wpm - AVG_TYPING_WPM) / AVG_TYPING_WPM) * 100) : 0;

	return {
		wpm,
		wpmDisplay: String(wpm),
		minutesSaved,
		minutesSavedDisplay: String(minutesSaved),
		modeDisplay,
		modeDetail,
		fasterThanAvg: `${fasterPercent}% faster than avg typer`,
	};
}

/**
 * Hook: captures a hidden DOM element and prompts the user to
 * download it as a PNG image.
 *
 * Usage:
 * ```tsx
 * const { imageRef, shareAsImage } = useStatsShare()
 *
 * return (
 *   <>
 *     <button onClick={() => shareAsImage('voice-typer-stats')}>Share</button>
 *     <div ref={imageRef} style={{ position: 'fixed', left: -9999, top: 0 }}>
 *       <StatsShareImage stats={...} />
 *     </div>
 *   </>
 * )
 * ```
 */
export function useStatsShare() {
	const imageRef = useRef<HTMLDivElement>(null);

	const shareAsImage = useCallback(async (filename = "voice-typer-stats") => {
		const el = imageRef.current;
		if (!el) return;

		try {
			const dataUrl = await toPng(el, {
				quality: 1,
				pixelRatio: 2,
				cacheBust: true,
			});

			// Trigger download via anchor element
			const link = document.createElement("a");
			link.download = `${filename}.png`;
			link.href = dataUrl;
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);
		} catch (err) {
			console.error("[StatsShare] Failed to generate image:", err);
		}
	}, []);

	return { imageRef, shareAsImage } as const;
}
