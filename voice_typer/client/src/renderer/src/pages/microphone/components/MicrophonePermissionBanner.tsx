// OS-level microphone-permission banner.
//
// PVT-036 (Fix 3): renders a destructive banner with platform-specific
// guidance + a deep-link button to the OS privacy settings when the
// renderer can prove the OS has denied microphone access
// (``micPermission === "denied"``). ``"prompt"`` / ``"unknown"`` do
// not render the banner — ``"prompt"`` is the user's first-run chance
// to grant, ``"unknown"`` means the API is unavailable (e.g. Linux
// WebKitGTK) and a false-positive banner would be worse than silence.
//
// Platform detection is via ``navigator.userAgent``: macOS and Windows
// expose a deep-link URL scheme to the OS privacy settings; Linux has
// no equivalent standard, so the button is omitted (the message text
// still tells the user where to look).

import { AlertCircleIcon, Settings03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t } from "@/i18n/i18n";
import type { MicPermission } from "../hooks/useMicrophonePermission";

export interface MicrophonePermissionBannerProps {
	micPermission: MicPermission;
}

export function MicrophonePermissionBanner({
	micPermission,
}: MicrophonePermissionBannerProps) {
	if (micPermission !== "denied") return null;

	const ua =
		typeof navigator !== "undefined" ? navigator.userAgent.toLowerCase() : "";

	let message: string;
	if (ua.includes("mac")) {
		message = t("microphone.permissionDeniedMessageMacos");
	} else if (ua.includes("win")) {
		message = t("microphone.permissionDeniedMessageWindows");
	} else if (ua.includes("linux")) {
		message = t("microphone.permissionDeniedMessageLinux");
	} else {
		message = t("microphone.permissionDeniedMessage");
	}

	// macOS and Windows expose a deep-link URL scheme to the OS privacy
	// settings. Linux has no equivalent standard, so we omit the button.
	let deepLink: string | null = null;
	if (ua.includes("mac")) {
		deepLink =
			"x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone";
	} else if (ua.includes("win")) {
		deepLink = "ms-settings:privacy-microphone";
	}

	return (
		<div
			role="alert"
			className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 space-y-2"
		>
			<div className="flex items-start gap-2">
				<HugeiconsIcon
					icon={AlertCircleIcon}
					strokeWidth={1.625}
					className="h-4 w-4 shrink-0 mt-0.5 text-destructive"
				/>
				<div className="flex-1 space-y-1">
					<p className="text-sm font-semibold text-destructive">
						{t("microphone.permissionDeniedTitle")}
					</p>
					<p className="text-xs text-(--text-primary)">{message}</p>
				</div>
			</div>
			{deepLink && (
				<a
					href={deepLink}
					aria-label={t("microphone.openSettingsAria")}
					className="inline-flex items-center gap-1.5 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
				>
					<HugeiconsIcon
						icon={Settings03Icon}
						strokeWidth={1.625}
						className="h-3.5 w-3.5"
					/>
					{t("microphone.openSettings")}
				</a>
			)}
		</div>
	);
}
