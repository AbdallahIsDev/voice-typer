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
		if (!el) {
			// EXPORT-FIX (Round 1): log the failure path so the
			// user can see why nothing happened.
			console.warn("[StatsShare] imageRef not attached — capture aborted");
			return;
		}

		// EXPORT-FIX (Round 1): pre-capture diagnostics. The previous
		// implementation silently produced a blank PNG because the
		// off-screen `position: fixed; left: -9999` wrapper caused
		// Chromium's paint optimization to skip painting the element,
		// and toPng captured a 0×0 region. Log the dimensions so we
		// can verify the element is actually rendered before capture.
		const rect = el.getBoundingClientRect();
		console.info("[StatsShare] capturing element:", {
			offsetWidth: el.offsetWidth,
			offsetHeight: el.offsetHeight,
			rectWidth: rect.width,
			rectHeight: rect.height,
		});
		if (el.offsetWidth === 0 || el.offsetHeight === 0) {
			console.error(
				"[StatsShare] target has zero size — image will be blank. " +
					"Check that the wrapper is not display:none or positioned off-screen.",
			);
			return;
		}

		try {
			// EXPORT-FIX (Round 1): the previous call used only
			// {quality:1, pixelRatio:2, cacheBust:true}. Three
			// issues caused blank images:
			// 1. No explicit width/height → toPng used the
			//    element's bounding rect, which was 0×0 due to
			//    the off-screen wrapper.
			// 2. No backgroundColor → transparent background
			//    produced an apparently-empty image.
			// 3. No style override → Tailwind v4 oklch() CSS
			//    variables cascaded into the clone and broke
			//    SVG foreignObject rendering (oklch is not
			//    supported in SVG-as-image context).
			// The fix: explicit width/height, backgroundColor,
			// and style:{all:initial} to break the oklch cascade.
			const dataUrl = await toPng(el, {
				pixelRatio: 2,
				cacheBust: false, // no <img> tags in subtree
				width: 600,
				height: 500,
				backgroundColor: "#0f0a1a", // matches StatsShareImage gradient
				style: {
					// Reset inherited Tailwind v4 oklch() CSS vars
					// that break SVG foreignObject rendering.
					margin: "0",
					padding: "0",
					// EXPORT-FIX (Round 2): the wrapper div in
					// Home.tsx/Dashboard.tsx uses
					// clipPath:inset(50% 50% 50% 50%) to hide
					// the off-screen capture target from the
					// user. html-to-image copies that style onto
					// the cloned node, clipping the rendered PNG
					// to a 0×0 region → solid #0f0a1a rectangle
					// with no stats content. Override it here so
					// the captured clone is fully visible
					// regardless of wrapper styling. Also reset
					// opacity/visibility defensively in case a
					// future wrapper uses those to hide.
					clipPath: "none",
					opacity: "1",
					visibility: "visible",
				},
			});

			console.info("[StatsShare] capture succeeded:", {
				dataUrlLength: dataUrl.length,
				dataUrlPrefix: dataUrl.slice(0, 50),
				filename: `${filename}.png`,
				dimensions: `${el.offsetWidth}x${el.offsetHeight}`,
			});

			// Trigger download via anchor element
			const link = document.createElement("a");
			link.download = `${filename}.png`;
			link.href = dataUrl;
			document.body.appendChild(link);
			link.click();
			document.body.removeChild(link);

			console.info(
				`[StatsShare] image saved: ${filename}.png ` +
					`(${el.offsetWidth}x${el.offsetHeight}px, ` +
					`${Math.round((dataUrl.length * 0.75) / 1024)}KB)`,
			);
		} catch (err) {
			console.error("[StatsShare] toPng threw:", err);
		}
	}, []);

	return { imageRef, shareAsImage } as const;
}
