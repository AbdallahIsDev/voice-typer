import { Mic02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import {
	RADIO_GROUP_ITEM_SELECTOR,
	SelectableRow,
} from "@/components/common/SelectableRow";
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

	return (
		// The a11y pair (nested radio is the accessible control; row
		// click is pointer-only convenience) + the skip-nested-control
		// click gating live in the shared SelectableRow wrapper.
		<SelectableRow
			ignoreClicksFrom={[RADIO_GROUP_ITEM_SELECTOR]}
			onRowSelect={() => {
				if (disabled || checked) return;
				onSelect();
			}}
			className={cn(
				"flex items-center gap-3 px-4 py-2 transition-colors",
				disabled || checked
					? "cursor-default"
					: "cursor-pointer hover:bg-foreground/5",
			)}
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
		</SelectableRow>
	);
}
