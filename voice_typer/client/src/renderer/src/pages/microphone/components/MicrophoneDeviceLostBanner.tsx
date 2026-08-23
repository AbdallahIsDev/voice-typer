// Device-lost recovery banner for the Microphone page.
//
// Rendered while ``useDeviceLostStore`` carries a loss: the backend
// exhausted its retries for the active microphone (unplug / Bluetooth
// power-off / driver reset) and published ``device_lost``. The level
// monitor is paused for the same condition — this banner is the
// recovery affordance that brings the meter back.
//
// Presentational only — visibility + the retry callback are passed in
// by the page. Retry clears the store flag + refreshes the mic list;
// the level-monitor effect then restarts on its own (its ``paused``
// gate flips false). If the mic is still gone, the backend re-emits
// and the banner returns.

import { MicOff01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t } from "@/i18n/i18n";

export interface MicrophoneDeviceLostBannerProps {
	visible: boolean;
	/** Clear the lost state + restart monitoring / refresh devices. */
	onRetry: () => void;
}

export function MicrophoneDeviceLostBanner({
	visible,
	onRetry,
}: MicrophoneDeviceLostBannerProps) {
	if (!visible) return null;

	return (
		<div
			role="alert"
			className="rounded-lg border border-warning/30 bg-warning/10 p-4 space-y-2"
		>
			<div className="flex items-start gap-2">
				<HugeiconsIcon
					icon={MicOff01Icon}
					strokeWidth={1.625}
					className="h-4 w-4 shrink-0 mt-0.5 text-warning"
				/>
				<div className="flex-1 space-y-1">
					<p className="text-sm font-semibold text-warning">
						{t("microphone.deviceLostTitle")}
					</p>
					<p className="text-xs text-(--text-primary)">
						{t("microphone.deviceLostDescription")}
					</p>
				</div>
			</div>
			<button
				type="button"
				onClick={onRetry}
				aria-label={t("microphone.retry")}
				className="inline-flex items-center gap-1.5 rounded-md border border-warning/30 bg-warning/5 px-3 py-1.5 text-xs font-medium text-warning hover:bg-warning/10 transition-colors"
			>
				{t("microphone.retry")}
			</button>
		</div>
	);
}
