import { Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import type { MicrophoneDevice } from "@/types/config";

interface MicrophoneListItemProps {
	mic: MicrophoneDevice;
	/** Whether the parent's active mic is System Default (null) */
	isSystemDefault: boolean;
	/** Called when the user clicks "Use" */
	onSelect: (micId: string) => void;
}
export function MicrophoneListItem({
	mic,
	isSystemDefault,
	onSelect,
}: MicrophoneListItemProps) {
	const micId = mic.id ?? String(mic.index);

	const handleSelect = () => onSelect(micId);

	return (
		<div className="flex items-center gap-3 px-3.5 py-2.5">
			<HugeiconsIcon
				icon={Mic02Icon}
				strokeWidth={2}
				className="h-4 w-4 shrink-0 text-(--text-muted)"
			/>
			<div className="flex flex-col flex-1 min-w-0 gap-1">
				<div className="flex items-center gap-2">
					<p className="text-sm font-medium text-(--text-primary) truncate">
						{mic.name}
					</p>
					{mic.default && !isSystemDefault && (
						// text-accent-foreground (not text-white) — --accent maps
						// to var(--primary) in every theme block, so the badge
						// foreground must be its paired token to stay
						// contrast-safe in light / dark / custom themes.
						<span className="shrink-0 inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-foreground">
							{t("microphone.systemDefault")}
						</span>
					)}
				</div>
				<p className="text-xs text-(--text-muted)">
					{t("microphone.channels")} {mic.channels ?? 1} &middot;{" "}
					{t("microphone.rateHz")} {mic.rate ?? 44100}Hz
				</p>
			</div>
			<Button
				variant="outline"
				size="sm"
				className="shrink-0 text-(--text-muted)"
				onClick={handleSelect}
				// BG-45: per-mic accessible name so screen-reader users
				// don't hear N identical "Use" buttons with no context
				// (the row's mic name is announced separately, but the
				// action button still needs its own name).
				aria-label={t("microphone.useAria", { name: mic.name })}
			>
				{t("microphone.use")}
			</Button>
		</div>
	);
}
