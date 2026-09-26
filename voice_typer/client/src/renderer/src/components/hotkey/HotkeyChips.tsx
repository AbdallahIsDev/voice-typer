import { Fragment } from "react";
import { Kbd, KbdGroup } from "@/components/common/Kbd";
import { formatHotkeyForPlatform } from "./hotkey-utils";

interface HotkeyChipsProps {
	keys: string;
	/** Optional extra classes applied to each chip group / chip. */
	className?: string;
}

function HotkeyCombo({
	keys,
	className,
}: {
	keys: string;
	className?: string;
}) {
	const parts = keys
		.split("+")
		.map((k) => k.trim())
		.filter((k) => k.length > 0);
	if (parts.length <= 1) {
		return <Kbd className={className}>{parts[0] ?? keys}</Kbd>;
	}
	return (
		<KbdGroup className={className}>
			{parts.map((part) => (
				<Kbd key={part}>{part}</Kbd>
			))}
		</KbdGroup>
	);
}

export function HotkeyChips({ keys, className }: HotkeyChipsProps) {
	// Platform transform FIRST (before splitting): on macOS the
	// modifiers become glyphs joined without "+" ("Ctrl+B" → "⌃B"),
	// matching the Sidebar's formatHotkey rendering; on Windows/Linux
	// this is a no-op. Already-formatted glyph input passes through
	// unchanged (idempotent).
	const alternatives = formatHotkeyForPlatform(keys).split(" / ");
	return (
		<>
			{alternatives.map((alt, i) => (
				<Fragment key={alt}>
					{i > 0 && (
						<span aria-hidden className="text-xs text-muted-foreground">
							{" / "}
						</span>
					)}
					<HotkeyCombo keys={alt} className={className} />
				</Fragment>
			))}
		</>
	);
}
