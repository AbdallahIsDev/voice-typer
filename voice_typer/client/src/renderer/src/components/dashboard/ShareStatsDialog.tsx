// ShareStatsDialog — the Share Stats trigger + preview popup.
//
// Clicking the Share icon opens a dialog showing a LIVE preview of the
// exact image that will be exported (no surprises after the fact),
// plus the export actions and direct social share targets.
//
// Orientation-aware layout: the preview is measured at render time; a
// landscape image (the current 1200×630 export) stacks the action
// controls BELOW the preview, a portrait image places them to the SIDE.
// The measurement is done via ResizeObserver so a future aspect-ratio
// change reflows automatically.
//
// Social targets (WhatsApp / Telegram / X / Facebook): the platform
// share-composer URLs do NOT support attaching an image from a desktop
// web/Electron context — every platform's web intent is text/URL-only.
// Each social button therefore uses the graceful fallback: copy the
// image to the clipboard, open the platform's composer in the OS
// browser (routed through the main process's `setWindowOpenHandler` →
// `shell.openExternal`), and toast an instruction to paste the image
// into the composer. All four platforms use this fallback — there is
// no desktop-web path that attaches the image directly.
import {
	Copy01Icon,
	Download03Icon,
	FacebookIcon,
	FileDownloadIcon,
	NewTwitterIcon,
	Share08Icon,
	TelegramIcon,
	WhatsappIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { APP_NAME } from "@/branding";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
	DialogTrigger,
} from "@/components/ui/dialog";
import { STATS_IMAGE_FILENAME } from "@/hooks/useStatsShare";
import { t } from "@/i18n/i18n";
import type { StatsThemePalette } from "@/lib/theme-palette";
import { cn } from "@/lib/utils";
import type { ShareStats } from "@/types/stats";
import { StatsShareImage } from "./StatsShareImage";

/** The three export actions, wired by the page that owns the capture
 * target (see `useStatsShare`). Each returns a value the dialog uses to
 * decide the toast: the saved path for download / save-as, `true` for
 * a successful clipboard copy. */
export interface ShareStatsActions {
	downloadImage: (filename?: string) => Promise<string | null>;
	saveImageAs: (filename?: string) => Promise<string | null>;
	copyImageToClipboard: (filename?: string) => Promise<boolean>;
	revealInFolder?: (filePath: string) => Promise<void>;
}

export interface ShareStatsDialogProps {
	actions: ShareStatsActions;
	/** The computed share stats — rendered as the live preview. */
	stats: ShareStats | null;
	/** Live theme palette so the preview matches the exported PNG. */
	palette: StatsThemePalette;
	/** Disabled when there is nothing shareable (canShareStats false). */
	disabled?: boolean;
}

/** The exported image's fixed capture width (mirrors useStatsShare). */
const EXPORT_WIDTH = 1200;

/** Social share intents. Web intents are text/URL-only — the image is
 * attached via the clipboard fallback (see header comment). */
const SOCIAL_TARGETS: {
	key: string;
	labelKey: string;
	icon: typeof WhatsappIcon;
	url: (caption: string) => string;
}[] = [
	{
		key: "whatsapp",
		labelKey: "stats.shareImage.socialWhatsapp",
		icon: WhatsappIcon,
		url: (c) => `https://wa.me/?text=${encodeURIComponent(c)}`,
	},
	{
		key: "telegram",
		labelKey: "stats.shareImage.socialTelegram",
		icon: TelegramIcon,
		url: (c) => `https://t.me/share/url?text=${encodeURIComponent(c)}`,
	},
	{
		key: "x",
		labelKey: "stats.shareImage.socialX",
		icon: NewTwitterIcon,
		url: (c) =>
			`https://twitter.com/intent/tweet?text=${encodeURIComponent(c)}`,
	},
	{
		key: "facebook",
		labelKey: "stats.shareImage.socialFacebook",
		icon: FacebookIcon,
		url: (c) =>
			`https://www.facebook.com/sharer/sharer.php?quote=${encodeURIComponent(c)}`,
	},
];

export function ShareStatsDialog({
	actions,
	stats,
	palette,
	disabled,
}: ShareStatsDialogProps) {
	const [open, setOpen] = useState(false);
	const containerRef = useRef<HTMLDivElement>(null);
	const previewRef = useRef<HTMLDivElement>(null);
	// Preview fit scale (the 1200px-wide image scaled to the dialog).
	const [scale, setScale] = useState(0.5);
	const [previewHeight, setPreviewHeight] = useState(630);
	// True when the measured preview is taller than wide.
	const [isPortrait, setIsPortrait] = useState(false);

	// Measure the preview's natural aspect ratio + fit the container
	// width. ResizeObserver keeps both correct across window/dialog
	// resizes; jsdom lacks ResizeObserver (tests) — the initial
	// landscape defaults apply there.
	useEffect(() => {
		if (!open) return;
		const measure = () => {
			const box = containerRef.current;
			const el = previewRef.current;
			if (!box || !el) return;
			const w = el.offsetWidth || EXPORT_WIDTH;
			const h = el.offsetHeight || 630;
			setPreviewHeight(h);
			setIsPortrait(h > w);
			setScale(Math.min(1, (box.clientWidth - 16) / EXPORT_WIDTH));
		};
		measure();
		if (typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(measure);
		if (containerRef.current) ro.observe(containerRef.current);
		return () => ro.disconnect();
		// `stats` is intentionally NOT a dep: the ResizeObserver on the
		// container fires when the preview's scaled height changes, so
		// content changes while the dialog is open re-measure without
		// re-running this effect.
	}, [open]);

	const handleDownload = async () => {
		const path = await actions.downloadImage(STATS_IMAGE_FILENAME);
		if (!path) return;
		toast.success(t("stats.shareImage.savedToDownloads"), {
			action: {
				label: t("stats.shareImage.showInFolder"),
				onClick: () => {
					void actions.revealInFolder?.(path);
				},
			},
		});
	};

	const handleCopy = async () => {
		const ok = await actions.copyImageToClipboard(STATS_IMAGE_FILENAME);
		if (ok) toast.success(t("stats.shareImage.copiedToClipboard"));
	};

	const handleSaveAs = async () => {
		await actions.saveImageAs(STATS_IMAGE_FILENAME);
	};

	// Fallback flow for every social platform (see header comment):
	// copy the image, open the composer with a prefilled caption, and
	// tell the user to paste.
	const handleSocial = async (target: (typeof SOCIAL_TARGETS)[number]) => {
		const ok = await actions.copyImageToClipboard(STATS_IMAGE_FILENAME);
		if (!ok) {
			toast.error(t("stats.shareImage.copyFailed"));
			return;
		}
		const caption = t("stats.shareImage.socialCaption", {
			appName: APP_NAME,
		});
		window.open(target.url(caption), "_blank", "noopener,noreferrer");
		toast.success(
			t("stats.shareImage.socialCopiedTo", {
				platform: t(target.labelKey),
			}),
		);
	};

	return (
		<Dialog open={open} onOpenChange={setOpen}>
			<DialogTrigger asChild>
				<Button
					variant="outline"
					size="icon"
					disabled={disabled}
					aria-label={t("stats.shareImage.shareTooltip")}
					className="text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Share08Icon}
						strokeWidth={1.625}
						className="h-4 w-4 shrink-0"
					/>
				</Button>
			</DialogTrigger>
			<DialogContent
				size="lg"
				className="sm:max-w-3xl bg-(--bg-subtle) ring-border/10"
			>
				<DialogHeader>
					<DialogTitle>{t("stats.shareImage.previewTitle")}</DialogTitle>
					<DialogDescription>
						{t("stats.shareImage.previewDescription")}
					</DialogDescription>
				</DialogHeader>

				<div className={cn("flex flex-col gap-4", isPortrait && "sm:flex-row")}>
					{/* Live preview of the exact exported image. The scaled
						image is position:absolute so its 1200×630 LAYOUT box
						never stretches the dialog; the frame's height is set
						explicitly to the scaled height. (Previously the
						unscaled layout height + a spacer made the frame ~2x
						as tall as the visible image — a huge blank gap — and
						pushed the sibling action column to 1200px wide,
						stretching the buttons edge-to-edge past the dialog.) */}
					<div
						ref={containerRef}
						className={cn(
							"relative w-full overflow-hidden rounded-xl border border-border/10 bg-black/20",
							isPortrait && "sm:w-1/2",
						)}
						style={{ height: Math.round(previewHeight * scale) }}
					>
						{stats && (
							<div
								ref={previewRef}
								className="absolute left-0 top-0"
								style={{
									width: EXPORT_WIDTH,
									transform: `scale(${scale})`,
									transformOrigin: "top left",
								}}
							>
								<StatsShareImage stats={stats} palette={palette} />
							</div>
						)}
					</div>

					{/* Export + social actions — framed (rounded border +
						padding) so the buttons read as one coherent block
						tied to the preview, not full-bleed fragments. */}
					<div
						className={cn(
							"flex flex-col gap-2.5 rounded-xl border border-border/10 bg-black/20 p-3",
							isPortrait ? "sm:w-1/2" : "w-full",
						)}
					>
						<Button onClick={handleDownload} className="w-full justify-start">
							<HugeiconsIcon
								icon={Download03Icon}
								className="h-4 w-4 shrink-0"
							/>
							{t("stats.shareImage.downloadImage")}
						</Button>
						<Button
							variant="outline"
							onClick={handleCopy}
							className="w-full justify-start"
						>
							<HugeiconsIcon icon={Copy01Icon} className="h-4 w-4 shrink-0" />
							{t("stats.shareImage.copyImage")}
						</Button>
						<Button
							variant="outline"
							onClick={handleSaveAs}
							className="w-full justify-start"
						>
							<HugeiconsIcon
								icon={FileDownloadIcon}
								className="h-4 w-4 shrink-0"
							/>
							{t("stats.shareImage.saveAs")}
						</Button>

						<div aria-hidden="true" className="my-1 h-px bg-border/10" />
						<p className="text-[11px] font-medium tracking-wide text-(--text-muted) uppercase">
							{t("stats.shareImage.socialShare")}
						</p>
						<div className="grid grid-cols-2 gap-2">
							{SOCIAL_TARGETS.map((target) => (
								<Button
									key={target.key}
									variant="outline"
									size="sm"
									onClick={() => void handleSocial(target)}
									className="justify-start"
								>
									<HugeiconsIcon
										icon={target.icon}
										className="h-4 w-4 shrink-0"
									/>
									{t(target.labelKey)}
								</Button>
							))}
						</div>
					</div>
				</div>
			</DialogContent>
		</Dialog>
	);
}
