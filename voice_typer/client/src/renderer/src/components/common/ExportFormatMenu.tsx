import { Download01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";

interface ExportFormatMenuProps {
	onExport: (format: "json" | "csv") => void | Promise<void>;
	disabled?: boolean;
}
export default function ExportFormatMenu({
	onExport,
	disabled,
}: ExportFormatMenuProps) {
	const [show, setShow] = useState(false);
	const menuRef = useRef<HTMLDivElement>(null);
	const btnRef = useRef<HTMLButtonElement>(null);

	// Close on click outside
	useEffect(() => {
		if (!show) return;
		const close = (e: MouseEvent) => {
			if (
				menuRef.current &&
				!menuRef.current.contains(e.target as Node) &&
				btnRef.current &&
				!btnRef.current.contains(e.target as Node)
			) {
				setShow(false);
			}
		};
		// Use a microtask delay so the current click that opened the menu
		// doesn't immediately close it
		const id = setTimeout(() => document.addEventListener("click", close), 0);
		return () => {
			clearTimeout(id);
			document.removeEventListener("click", close);
		};
	}, [show]);

	const handleToggle = () => setShow((prev) => !prev);

	const handleExportJson = () => {
		setShow(false);
		onExport("json");
	};

	const handleExportCsv = () => {
		setShow(false);
		onExport("csv");
	};

	return (
		<div className="relative">
			<Button
				ref={btnRef}
				variant="outline"
				size="sm"
				onClick={handleToggle}
				disabled={disabled}
				// FIX: match the muted-text / white-on-hover style of the
				// sibling buttons in the same action row (Favorites, Clear
				// All, Add Word, etc.).  Without this, the outline variant
				// inherits the default bright --text-primary color which
				// looks out of place next to the muted siblings.
				className="gap-2 text-(--text-muted) hover:text-(--text-primary)"
				aria-haspopup="menu"
				aria-expanded={show}
			>
				<HugeiconsIcon
					icon={Download01Icon}
					strokeWidth={2}
					className="h-4 w-4"
				/>
				{t("exportFormat.export")}
			</Button>
			{show && (
				<div
					ref={menuRef}
					role="menu"
					aria-label={t("a11y.exportFormat")}
					className="absolute right-0 top-full mt-1 z-10 w-30 rounded-xl border border-border bg-(--bg-subtle) shadow-lg overflow-hidden"
				>
					<button
						type="button"
						role="menuitem"
						onClick={handleExportJson}
						className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
					>
						{t("exportFormat.json")}
					</button>
					<button
						type="button"
						role="menuitem"
						onClick={handleExportCsv}
						className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
					>
						{t("exportFormat.csv")}
					</button>
				</div>
			)}
		</div>
	);
}
