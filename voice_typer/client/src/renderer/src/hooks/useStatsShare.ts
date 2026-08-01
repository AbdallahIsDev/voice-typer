import { toPng } from "html-to-image";
import { useCallback, useRef } from "react";
import { t } from "@/i18n/i18n";
import type { TodayStats } from "@/types/ipc";
import type { ShareStats } from "@/types/stats";

const AVG_TYPING_WPM = 40;

/** Cloud ASR backends. */
const CLOUD_BACKENDS = new Set(["openai", "groq", "deepgram"]);

/**
 * Decide whether the "Share stats" button should be visible.
 *
 * Fix #25-3: previously the share button in Dashboard.tsx was gated on
 * `data.todayCount > 0`, which hid the button on days when the user
 * hadn't dictated yet BUT had past transcriptions (totalCount > 0).
 * The share image still produces a meaningful summary in that case
 * (lifetime stats, 7-day activity chart, streak, active days) — the
 * only zero field is today's WPM/minutes-saved. Hiding the button
 * silently degraded the shareable-moment UX for any user who opens
 * the dashboard before their first dictation of the day.
 *
 * The button should be visible when EITHER:
 *   - the user has dictated today (todayCount > 0), OR
 *   - the user has historical transcriptions (totalCount > 0)
 *
 * This helper centralises that policy so Dashboard.tsx and Home.tsx
 * (and any future share-aware page) don't re-implement it
 * independently and drift out of sync.
 *
 * @example
 *   const showShare = canShareStats({ todayCount: data.todayCount, totalCount: data.totalCount });
 */
export function canShareStats(opts: {
	todayCount: number;
	totalCount: number;
}): boolean {
	return opts.todayCount > 0 || opts.totalCount > 0;
}

/**
 * Pure function: compute shareable stats from today's data + config.
 *
 * This is intentionally a pure function (no hooks, no side effects)
 * so it's easy to test and reuse.
 *
 * : the user-visible strings (mode display, faster-than-avg)
 * previously hardcoded English ("Cloud", "Offline", "Cloud API",
 * "Local Model", "X% faster than avg typer") regardless of the
 * active locale. They now resolve through ``t()`` so the share image
 * renders in the user-selected UI language.
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
	const modeDisplay = isCloud
		? t("stats.shareImage.cloudMode")
		: t("stats.shareImage.offlineMode");
	const modeDetail = isCloud
		? t("stats.shareImage.cloudApi")
		: t("stats.shareImage.localModel");

	const fasterPercent =
		wpm > 0 ? Math.round(((wpm - AVG_TYPING_WPM) / AVG_TYPING_WPM) * 100) : 0;

	return {
		wpm,
		wpmDisplay: String(wpm),
		minutesSaved,
		minutesSavedDisplay: String(minutesSaved),
		modeDisplay,
		modeDetail,
		fasterThanAvg: t("stats.shareImage.fasterThanAvg", {
			percent: String(fasterPercent),
		}),
	};
}

/**
 * Hook: captures a hidden DOM element and prompts the user to
 * download it as a PNG image.
 *
 * : the hook now accepts an optional ``onError`` callback that
 * fires when the capture / share / download pipeline throws. Callers
 * should pass a handler that surfaces ``t('stats.shareImage.captureFailed')``
 * (or a context-specific message) to the user via a toast — previously
 * errors were silently swallowed (only ``console.error``), so the user
 * clicked "Share" and nothing visibly happened. The hook also
 * feature-detects ``navigator.canShare({files: [...]})`` and prefers
 * the native OS share sheet when available (mobile / macOS / Windows
 * share dialog); only when the native share is unavailable does it
 * fall back to the anchor-download path.
 *
 * Usage:
 * ```tsx
 * const { imageRef, shareAsImage } = useStatsShare({
 *   onError: (msg) => toast.error(msg),
 * })
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
export interface UseStatsShareOptions {
	/**
	 * : invoked when the capture / share / download pipeline
	 * fails. The argument is a localized error message
	 * (``t('stats.shareImage.captureFailed')`` by default) suitable
	 * for surfacing via ``toast.error``. Callers may pass a
	 * custom message to override the default.
	 */
	onError?: (message: string) => void;
}

export function useStatsShare(options?: UseStatsShareOptions) {
	const imageRef = useRef<HTMLDivElement>(null);

	const shareAsImage = useCallback(
		async (filename = "voice-typer-stats") => {
			const onError = options?.onError;
			const el = imageRef.current;
			if (!el) {
				// EXPORT-FIX: log the failure path so the
				// user can see why nothing happened.
				console.warn("[StatsShare] imageRef not attached — capture aborted");
				onError?.(t("stats.shareImage.captureFailed"));
				return;
			}

			// EXPORT-FIX: pre-capture diagnostics. The previous
			// implementation silently produced a blank PNG because the
			// off-screen `position: fixed; left: -9999` wrapper caused
			// Chromium's paint optimization to skip painting the element,
			// and toPng captured a 0×0 region. Log the dimensions so we
			// can verify the element is actually rendered before capture.
			const rect = el.getBoundingClientRect();
			//dev-only diagnostic — the previous build shipped these
			// console.info calls in production, where they leaked user-data
			// shape (offsetWidth, etc.) to the renderer DevTools console of
			// anyone running the packaged app.
			if (import.meta.env.DEV) {
				console.info("[StatsShare] capturing element:", {
					offsetWidth: el.offsetWidth,
					offsetHeight: el.offsetHeight,
					rectWidth: rect.width,
					rectHeight: rect.height,
				});
			}
			if (el.offsetWidth === 0 || el.offsetHeight === 0) {
				console.error(
					"[StatsShare] target has zero size — image will be blank. " +
						"Check that the wrapper is not display:none or positioned off-screen.",
				);
				//surface the failure to the caller so the user
				// gets a visible toast instead of an invisible no-op.
				onError?.(t("stats.shareImage.captureFailed"));
				return;
			}

			try {
				// EXPORT-FIX: the previous call used only
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
						// EXPORT-FIX: the wrapper div in
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

				//dev-only success diagnostic.
				if (import.meta.env.DEV) {
					console.info("[StatsShare] capture succeeded:", {
						dataUrlLength: dataUrl.length,
						dataUrlPrefix: dataUrl.slice(0, 50),
						filename: `${filename}.png`,
						dimensions: `${el.offsetWidth}x${el.offsetHeight}`,
					});
				}

				//prefer the native OS share sheet
				// (mobile / macOS / Windows share dialog) when
				// the browser supports sharing files. This
				// lets the user send the image directly to
				// Messages / Mail / Slack / etc. instead of
				// being forced into a browser-download flow.
				// Fall back to the anchor-download path when
				// ``navigator.canShare`` reports the file
				// type is not shareable (or the API is
				// missing entirely — desktop Firefox).
				if (
					typeof navigator !== "undefined" &&
					typeof navigator.canShare === "function" &&
					typeof navigator.share === "function"
				) {
					try {
						const blob = await (await fetch(dataUrl)).blob();
						const file = new File([blob], `${filename}.png`, {
							type: "image/png",
						});
						const sharePayload = { files: [file] };
						if (navigator.canShare(sharePayload)) {
							await navigator.share(sharePayload);
							//dev-only post-share diagnostic.
							if (import.meta.env.DEV) {
								console.info(
									`[StatsShare] image shared via navigator.share: ${filename}.png`,
								);
							}
							return;
						}
					} catch (shareErr) {
						// ``navigator.share`` rejects with
						// ``AbortError`` when the user
						// dismisses the share sheet — that's
						// a user-initiated cancel, NOT a
						// failure. Suppress those silently;
						// surface any other error via the
						// download fallback below so the
						// user still ends up with the image.
						const name = shareErr instanceof Error ? shareErr.name : "";
						if (name !== "AbortError") {
							console.warn(
								"[StatsShare] navigator.share failed, falling back to download:",
								shareErr,
							);
						} else {
							// User cancelled — no further action.
							return;
						}
					}
				}

				// Fallback: trigger download via anchor element.
				const link = document.createElement("a");
				link.download = `${filename}.png`;
				link.href = dataUrl;
				document.body.appendChild(link);
				link.click();
				document.body.removeChild(link);

				//dev-only post-download diagnostic.
				if (import.meta.env.DEV) {
					console.info(
						`[StatsShare] image saved: ${filename}.png ` +
							`(${el.offsetWidth}x${el.offsetHeight}px, ` +
							`${Math.round((dataUrl.length * 0.75) / 1024)}KB)`,
					);
				}
			} catch (err) {
				console.error("[StatsShare] toPng threw:", err);
				//surface the failure to the caller so the
				// user gets a visible error toast instead of a
				// silent console.error.
				onError?.(t("stats.shareImage.captureFailed"));
			}
		},
		[options?.onError],
	);

	return { imageRef, shareAsImage } as const;
}
