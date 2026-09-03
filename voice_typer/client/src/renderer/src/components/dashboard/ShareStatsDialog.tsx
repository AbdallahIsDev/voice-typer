// ShareStatsDialog — the Share Stats trigger + preview popup.
//
// Clicking the Share icon opens a dialog showing a LIVE preview of the
// exact image that will be exported (no surprises after the fact),
// plus the export actions and direct social share targets.
//
// Preview-fit design: the export is a FIXED 1200×630 image, so the
// preview frame is sized by CSS `aspect-ratio: 1200 / 630` and the
// image is scaled to the frame width via a `--preview-scale` custom
// property written directly on the frame at attach time (before the
// first paint). The preview is therefore fully visible and exactly
// fitted from the very first frame — no clipping, no dead space, no
// delayed transform correcting its own layout. The landscape image
// stacks the action controls BELOW the preview.
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
import { useCallback, useLayoutEffect, useRef, useState } from "react";
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

/** The exported image's fixed capture dimensions (mirrors useStatsShare). */
const EXPORT_WIDTH = 1200;
const EXPORT_HEIGHT = 630;

/** Project repo — the shared link for platforms whose URL schemes
 * require a link (Telegram's t.me/share/url needs the `url` param). */
const GITHUB_REPO = "https://github.com/AbdallahIsDev/voice-typer";

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
		// The official share format is t.me/share/url?url=<url>&text=<text>
		// — the `url` param is REQUIRED. Without it Telegram's web
		// handler redirects to telegram.org (its homepage) instead of
		// opening the app's share/forward picker. Since the stats image
		// itself can't be attached via URL, we share the project repo as
		// the link (the caption carries the stats text).
		url: (c) =>
			`https://t.me/share/url?url=${encodeURIComponent(
				GITHUB_REPO,
			)}&text=${encodeURIComponent(c)}`,
	},
	{
		key: "x",
		labelKey: "stats.shareImage.socialTwitter",
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

	// Preview scale = container width ÷ export width. Written as a CSS
	// custom property DIRECTLY on the frame (not React state) so it is
	// in place before the first paint: the preview renders fully
	// visible and exactly fitted from the very first frame — no state
	// round-trip, no flash, no delayed correction. The frame itself is
	// sized by CSS `aspect-ratio` (the export's fixed 1200:630 ratio),
	// so its height is always right regardless of measurement timing.
	const applyScale = useCallback(() => {
		const box = containerRef.current;
		if (!box) return;
		// Exact fit — no fudge margin: the scaled image width equals
		// the frame's content width (a previous `- 16` margin left a
		// permanent black strip on the right of the frame).
		box.style.setProperty(
			"--preview-scale",
			String(Math.min(1, box.clientWidth / EXPORT_WIDTH)),
		);
	}, []);

	// Callback ref on the CONTAINER: fires when the portal node
	// attaches (commit phase, before paint). The container's width is
	// what determines the scale, so measuring it directly at attach
	// time guarantees the custom property is set before the first
	// visible frame — no dependence on a later ResizeObserver
	// delivery. (Radix mounts dialog content via Presence in a
	// follow-up commit, so a plain useLayoutEffect on `open` cannot
	// reliably see the ref; the callback ref fires exactly when the
	// node attaches.)
	const setContainerRef = (el: HTMLDivElement | null) => {
		containerRef.current = el;
		if (el) applyScale();
	};

	// Keep the scale correct across window/dialog resizes.
	// ResizeObserver notifications are delivered before paint, so a
	// resize never shows an intermediate mis-fitted frame.
	useLayoutEffect(() => {
		if (!open) return;
		if (typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(() => applyScale());
		if (containerRef.current) ro.observe(containerRef.current);
		return () => ro.disconnect();
	}, [open, applyScale]);

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
			<DialogContent size="lg" className="sm:max-w-4xl bg-(--bg) ring-border/5">
				<DialogHeader>
					<DialogTitle>{t("stats.shareImage.previewTitle")}</DialogTitle>
					<DialogDescription>
						{t("stats.shareImage.previewDescription")}
					</DialogDescription>
				</DialogHeader>

				<div className="flex flex-col gap-4">
					{/* Live preview of the exact exported image. The frame's
						CSS aspect-ratio (1200:630 — the export's fixed size)
						makes its height correct from the first frame with no
						JS measurement; the image is scaled to the frame width
						exactly via --preview-scale (set on the frame before
						first paint), so the whole image is always visible
						with no cropping and no dead space — and never needs a
						delayed transform to correct its own layout. The scaled
						image is position:absolute so its 1200×630 LAYOUT box
						never stretches the dialog. */}
					<div
						ref={setContainerRef}
						className="relative w-full overflow-hidden rounded-xl border border-border/5 bg-black/20"
						style={{
							aspectRatio: `${EXPORT_WIDTH} / ${EXPORT_HEIGHT}`,
						}}
					>
						{stats && (
							<div
								className="absolute left-0 top-0"
								style={{
									width: EXPORT_WIDTH,
									transform: "scale(var(--preview-scale, 0.5))",
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
					<div className="flex w-full flex-col gap-2.5 rounded-xl border border-border/5 bg-black/20 p-3">
						{/* Neutral/secondary style — Download, Copy, and Save As
						    are equally valid exports; none is privileged (the
						    previous accent/primary treatment visually pushed
						    users toward Download specifically). */}
						<Button
							variant="outline"
							onClick={handleDownload}
							className="w-full justify-start"
						>
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

						<div aria-hidden="true" className="h-px bg-border/5" />
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
									// Same icon→label gap as the local action buttons
									// (sm size defaults to gap-1; the local buttons
									// use the default gap-2).
									className="justify-start gap-2"
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
