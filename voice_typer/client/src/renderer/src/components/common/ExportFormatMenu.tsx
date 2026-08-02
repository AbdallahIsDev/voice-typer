import { Download01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { t } from "@/i18n/i18n";

interface ExportFormatMenuProps {
	onExport: (format: "json" | "csv") => void | Promise<void>;
	disabled?: boolean;
}

// Migrated from a hand-rolled menu to the shared Radix
// DropdownMenu wrapper (`ui/dropdown-menu.tsx`). Radix provides the
// WAI-ARIA Menu Button pattern out of the box: role="menu"/"menuitem",
// aria-haspopup/aria-expanded/aria-controls on the trigger, roving
// arrow-key navigation (with wrapping), Home/End jumps, Escape closes +
// restores focus to the trigger, and Tab closes the menu. On open, Radix
// already moves focus to the first menuitem, so no extra handler is needed.
export default function ExportFormatMenu({
	onExport,
	disabled,
}: ExportFormatMenuProps) {
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild disabled={disabled}>
				<Button
					variant="outline"
					size="sm"
					disabled={disabled}
					// Match the muted-text / white-on-hover style of the
					// sibling buttons in the same action row (Favorites, Clear
					// All, Add Word, etc.). Without this, the outline variant
					// inherits the default bright --text-primary color which
					// looks out of place next to the muted siblings.
					className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
				>
					<HugeiconsIcon
						icon={Download01Icon}
						strokeWidth={2}
						className="h-4 w-4"
					/>
					{t("exportFormat.export")}
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" aria-label={t("a11y.exportFormat")}>
				<DropdownMenuItem onSelect={() => onExport("json")}>
					{t("exportFormat.json")}
				</DropdownMenuItem>
				<DropdownMenuItem onSelect={() => onExport("csv")}>
					{t("exportFormat.csv")}
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
