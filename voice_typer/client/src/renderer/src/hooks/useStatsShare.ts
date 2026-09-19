import { toPng } from "html-to-image";
import { useCallback, useRef } from "react";
import { t } from "@/i18n/i18n";
import { compactNumber, formatDuration } from "@/lib/format";
import type { TodayStats } from "@/types/ipc";
import type { ShareStats } from "@/types/stats";

const AVG_TYPING_WPM = 40;

/** Cloud ASR backends. */
const CLOUD_BACKENDS = new Set(["openai", "groq", "deepgram"]);

/** Default export filename (the `.png` extension is appended by the
 * main process / fallback download). */
export const STATS_IMAGE_FILENAME = "voice-typer-stats";

export function canShareStats(opts: {
	todayCount: number;
	totalCount: number;
}): boolean {
	return opts.todayCount > 0 || opts.totalCount > 0;
}

/** Optional lifetime / setup metrics the share image can pull from the
 * Analytics page. Every field is optional, `computeShareStats` still
 * produces a valid summary from today's data alone. */
export interface ShareStatsExtras {
	/** All-time dictation count. */
	totalCount?: number;
	/** All-time character count. */
	totalChars?: number;
	/** All-time recording duration in seconds. */
	totalDuration?: number;
	/** Distinct active days. */
	activeDays?: number;
	/** Current day streak (0 = none). */
	currentStreak?: number;
	/** ASR model name. */
	model?: string;
	/** Device (e.g. "cpu"). */
	device?: string;
}

export function computeShareStats(
	todayStats: TodayStats,
	asrBackend: string,
	extras?: ShareStatsExtras,
): ShareStats {
	const durationMinutes = todayStats.duration / 60;
	const hasTodayActivity = todayStats.count > 0 && durationMinutes > 0;
	const wpm = hasTodayActivity
		? Math.round(todayStats.word_count / durationMinutes)
		: 0;

	const minutesSaved = hasTodayActivity
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

	const totalCount = extras?.totalCount ?? todayStats.count;
	const totalChars = extras?.totalChars ?? todayStats.chars;
	const totalDuration = extras?.totalDuration ?? todayStats.duration;
	const activeDays = extras?.activeDays ?? (totalCount > 0 ? 1 : 0);
	const currentStreak = extras?.currentStreak ?? 0;

	return {
		wpm,
		wpmDisplay: hasTodayActivity ? String(wpm) : "—",
		minutesSaved,
		minutesSavedDisplay: String(minutesSaved),
		modeDisplay,
		modeDetail,
		fasterThanAvg: hasTodayActivity
			? t("stats.shareImage.fasterThanAvg", {
					percent: String(fasterPercent),
				})
			: null,
		hasTodayActivity,
		dictations: compactNumber(totalCount, { localeAware: true }),
		activeDays: compactNumber(activeDays, { localeAware: true }),
		activeDaysDetail:
			currentStreak > 0
				? t("stats.shareImage.dayStreak", {
						count: String(currentStreak),
					})
				: null,
		chars: compactNumber(totalChars, { localeAware: true }),
		recordingTime: formatDuration(totalDuration),
		model: extras?.model ?? "",
		device: extras?.device ?? "",
	};
}

export function triggerAnchorDownload(dataUrl: string, filename: string): void {
	const link = document.createElement("a");
	link.download = `${filename}.png`;
	link.href = dataUrl;
	document.body.appendChild(link);
	link.click();
	document.body.removeChild(link);
}

export interface UseStatsShareOptions {
	onError?: (message: string) => void;
}

export function useStatsShare(options?: UseStatsShareOptions) {
	const imageRef = useRef<HTMLDivElement>(null);

	const onError = options?.onError;

	/** Capture the off-screen image element and return a PNG data URL
	 * (or `null` when the element is missing / zero-sized / capture
	 * throws, the failure is surfaced via `onError`). */
	const captureImage = useCallback(async (): Promise<string | null> => {
		const el = imageRef.current;
		if (!el) {
			console.warn(
				"[renderer:useStatsShare] imageRef not attached, capture aborted",
			);
			onError?.(t("stats.shareImage.captureFailed"));
			return null;
		}

		const rect = el.getBoundingClientRect();
		if (import.meta.env.DEV) {
			console.info("[renderer:useStatsShare] capturing element:", {
				offsetWidth: el.offsetWidth,
				offsetHeight: el.offsetHeight,
				rectWidth: rect.width,
				rectHeight: rect.height,
			});
		}
		if (el.offsetWidth === 0 || el.offsetHeight === 0) {
			console.error(
				"[renderer:useStatsShare] target has zero size, image will be blank. " +
					"Check that the wrapper is not display:none or positioned off-screen.",
			);
			onError?.(t("stats.shareImage.captureFailed"));
			return null;
		}

		try {
			const dataUrl = await toPng(el, {
				pixelRatio: 2,
				cacheBust: false, // no <img> tags in subtree
				width: 1200,
				height: 630,
				backgroundColor: "#000000", // overridden by the element's own theme background
				style: {
					margin: "0",
					padding: "0",
					// The wrapper div in Home.tsx/Dashboard.tsx uses
					// clipPath/offscreen positioning to hide the capture
					// target from the user. html-to-image copies those
					// styles onto the cloned node, clipping the rendered
					// PNG. Override them here so the captured clone is
					// fully visible.
					clipPath: "none",
					opacity: "1",
					visibility: "visible",
				},
			});

			if (import.meta.env.DEV) {
				console.info("[renderer:useStatsShare] capture succeeded:", {
					dataUrlLength: dataUrl.length,
					dataUrlPrefix: dataUrl.slice(0, 50),
					dimensions: `${el.offsetWidth}x${el.offsetHeight}`,
				});
			}
			return dataUrl;
		} catch (err) {
			console.error("[renderer:useStatsShare] toPng threw:", err);
			onError?.(t("stats.shareImage.captureFailed"));
			return null;
		}
	}, [onError]);

	/** Capture + instant-save to the OS Downloads folder (no dialog).
	 * Returns the saved file path, or `null` on failure. Falls back to
	 * an anchor download when the predecessor bridge is unavailable. */
	const downloadImage = useCallback(
		async (filename = STATS_IMAGE_FILENAME): Promise<string | null> => {
			const dataUrl = await captureImage();
			if (!dataUrl) return null;

			const bridge = window.window_?.saveStatsImage;
			if (typeof bridge === "function") {
				const res = await bridge(dataUrl, filename, "downloads");
				if (res?.success && res.path) return res.path;
				onError?.(res?.error ?? t("stats.shareImage.saveFailed"));
				return null;
			}
			triggerAnchorDownload(dataUrl, filename);
			return null;
		},
		[captureImage, onError],
	);

	/** Capture + native "Save As…" dialog. Returns the chosen path, or
	 * `null` (canceled → silent; failure → surfaced via `onError`). */
	const saveImageAs = useCallback(
		async (filename = STATS_IMAGE_FILENAME): Promise<string | null> => {
			const dataUrl = await captureImage();
			if (!dataUrl) return null;

			const bridge = window.window_?.saveStatsImage;
			if (typeof bridge === "function") {
				const res = await bridge(dataUrl, filename, "saveAs");
				if (res?.success && res.path) return res.path;
				if (res && !res.canceled) {
					onError?.(res?.error ?? t("stats.shareImage.saveFailed"));
				}
				return null;
			}
			triggerAnchorDownload(dataUrl, filename);
			return null;
		},
		[captureImage, onError],
	);

	/** Capture + put the PNG on the OS clipboard. Returns `true` on
	 * success. Browser fallback uses the async Clipboard API. (The
	 * filename is only meaningful for file-based actions, clipboard
	 * copy is content-only.) */
	const copyImageToClipboard = useCallback(async (): Promise<boolean> => {
		const dataUrl = await captureImage();
		if (!dataUrl) return false;

		const bridge = window.window_?.copyStatsImage;
		if (typeof bridge === "function") {
			const res = await bridge(dataUrl);
			if (res?.success) return true;
			onError?.(res?.error ?? t("stats.shareImage.copyFailed"));
			return false;
		}

		// Browser fallback: async Clipboard API with a ClipboardItem.
		try {
			const blob = await (await fetch(dataUrl)).blob();
			const item = new ClipboardItem({
				"image/png": blob,
			});
			await navigator.clipboard.write([item]);
			return true;
		} catch (err) {
			console.warn(
				"[renderer:useStatsShare] navigator.clipboard image write failed:",
				err,
			);
			onError?.(t("stats.shareImage.copyFailed"));
			return false;
		}
	}, [captureImage, onError]);

	const revealInFolder = useCallback(
		async (filePath: string): Promise<void> => {
			await window.window_?.revealStatsImage?.(filePath);
		},
		[],
	);

	return {
		imageRef,
		captureImage,
		downloadImage,
		saveImageAs,
		copyImageToClipboard,
		revealInFolder,
	} as const;
}
