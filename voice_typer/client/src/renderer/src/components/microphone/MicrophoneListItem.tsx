import { Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import type { MouseEvent } from "react";
import { RadioGroupItem } from "@/components/ui/radio-group";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { MicrophoneDevice } from "@/types/config";

interface MicrophoneListItemProps {
	mic: MicrophoneDevice;
	/** Whether this device is the currently-active selection. */
	checked: boolean;
	/** Show the "Default" badge (OS default device, not currently selected). */
	showDefaultBadge: boolean;
	/** Disables the row + its radio (e.g. while a test is recording). */
	disabled: boolean;
	/** Called when the row is activated (row click or radio change). */
	onSelect: () => void;
}

export function MicrophoneListItem({
	mic,
	checked,
	showDefaultBadge,
	disabled,
	onSelect,
}: MicrophoneListItemProps) {
	const micId = mic.id ?? String(mic.index);

	const handleRowClick = (event: MouseEvent<HTMLDivElement>) => {
		// Clicks that originate on the radio control itself are handled by
		// Radix (onValueChange); handling them here too would fire the
		// selection IPC twice for one click.
		if (
			(event.target as HTMLElement).closest('[data-slot="radio-group-item"]')
		) {
			return;
		}
		if (disabled || checked) return;
		onSelect();
	};

	return (
		// biome-ignore lint/a11y/noStaticElementInteractions: the nested RadioGroupItem is the accessible control (role=radio); the row click is pointer convenience.
		// biome-ignore lint/a11y/useKeyWithClickEvents: keyboard activation goes through the focused radio itself (Space/arrows via Radix); a keydown mirror here would double-fire the selection.
		<div
			className={cn(
				"flex items-center gap-3 px-3.5 py-2.5 transition-colors",
				disabled || checked
					? "cursor-default"
					: "cursor-pointer hover:bg-foreground/5",
			)}
			onClick={handleRowClick}
		>
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
					{showDefaultBadge && (
						// text-accent-foreground (not text-white) — --accent maps
						// to var(--primary) in every theme block, so the badge
						// foreground must be its paired token to stay
						// contrast-safe in light / dark / custom themes.
						// Deliberately NOT the "System Default" string: that
						// label names the sentinel selection row; this badge
						// only marks "this device is the OS default" (C-UI-2:
						// one meaning per label).
						<span className="shrink-0 inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold text-accent-foreground">
							{t("microphone.osDefaultBadge")}
						</span>
					)}
				</div>
			</div>
			<RadioGroupItem value={micId} disabled={disabled} aria-label={mic.name} />
		</div>
	);
}
