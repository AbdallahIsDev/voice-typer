// ShareStatsMenu — the Share Stats trigger: an icon-only button
// (tooltip on hover) that opens a dropdown with the share actions.
//
// Actions:
//   - Download image  → instant-save to the OS Downloads folder (no
//     dialog) + "Saved to Downloads" toast with a "Show in folder"
//     action that reveals the file in the OS file manager.
//   - Copy image      → PNG onto the OS clipboard + "Copied to
//     clipboard" toast.
//   - Save As…        → native save dialog to pick a location.
//
// No OS share sheets (Twitter / Telegram / etc.) are shipped: the Web
// Share API (`navigator.share`) is not available to Electron renderers
// on Windows / Linux, and macOS's share sheet requires the main
// process to host the NSSharingServicePicker — neither is practical to
// wire into this Electron setup today. Download + Copy + Save As are
// real, working targets; the menu is a plain shadcn DropdownMenu so
// additional share targets can be added later without a redesign.
import {
	Copy01Icon,
	Download03Icon,
	FileDownloadIcon,
	Share08Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { STATS_IMAGE_FILENAME } from "@/hooks/useStatsShare";
import { t } from "@/i18n/i18n";

/** The three share actions, wired by the page that owns the capture
 * target (see `useStatsShare`). Each returns a value the menu uses to
 * decide the toast: the saved path for download / save-as, `true` for
 * a successful clipboard copy. */
export interface ShareStatsActions {
	downloadImage: (filename?: string) => Promise<string | null>;
	saveImageAs: (filename?: string) => Promise<string | null>;
	copyImageToClipboard: (filename?: string) => Promise<boolean>;
	revealInFolder?: (filePath: string) => Promise<void>;
}

export interface ShareStatsMenuProps {
	actions: ShareStatsActions;
	/** Disabled when there is nothing shareable (canShareStats false). */
	disabled?: boolean;
}

export function ShareStatsMenu({ actions, disabled }: ShareStatsMenuProps) {
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

	const trigger: ReactNode = (
		<DropdownMenuTrigger asChild>
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
		</DropdownMenuTrigger>
	);

	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<DropdownMenu>
					{trigger}
					<DropdownMenuContent align="end" side="bottom" className="min-w-52">
						<DropdownMenuItem onSelect={handleDownload}>
							<HugeiconsIcon
								icon={Download03Icon}
								className="h-4 w-4 text-(--text-muted)"
							/>
							{t("stats.shareImage.downloadImage")}
						</DropdownMenuItem>
						<DropdownMenuItem onSelect={handleCopy}>
							<HugeiconsIcon
								icon={Copy01Icon}
								className="h-4 w-4 text-(--text-muted)"
							/>
							{t("stats.shareImage.copyImage")}
						</DropdownMenuItem>
						<DropdownMenuSeparator />
						<DropdownMenuItem onSelect={handleSaveAs}>
							<HugeiconsIcon
								icon={FileDownloadIcon}
								className="h-4 w-4 text-(--text-muted)"
							/>
							{t("stats.shareImage.saveAs")}
						</DropdownMenuItem>
					</DropdownMenuContent>
				</DropdownMenu>
			</TooltipTrigger>
			<TooltipContent side="bottom">
				{t("stats.shareImage.shareTooltip")}
			</TooltipContent>
		</Tooltip>
	);
}
