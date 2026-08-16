import { Fragment } from "react";
import { Kbd, KbdGroup } from "@/components/ui/kbd";

interface HotkeyChipsProps {
	/**
	 * Pre-formatted hotkey string, e.g. `"Ctrl+Alt+V"` or
	 * `"Tab / Shift+Tab"`. `" / "` separates alternative bindings
	 * (rendered with a plain separator); `"+"` separates the individual
	 * keys of a combo (rendered as a `KbdGroup` of `Kbd` chips).
	 */
	keys: string;
	/** Optional extra classes applied to each chip group / chip. */
	className?: string;
}

/**
 * Renders one hotkey alternative ("Ctrl+Alt+V") as a `KbdGroup` of
 * `Kbd` chips with a muted "+" separator, or a single `Kbd` chip when
 * there is no combo ("Esc", "Caps Lock"). Falls back to a single chip
 * holding the whole string when the format is unexpected (e.g. an
 * i18n key that couldn't be resolved).
 */
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
			{parts.map((part, i) => (
				<Fragment key={part}>
					{i > 0 && (
						<span
							aria-hidden
							className="text-[11px] leading-none text-(--text-muted)"
						>
							+
						</span>
					)}
					<Kbd>{part}</Kbd>
				</Fragment>
			))}
		</KbdGroup>
	);
}

/**
 * HotkeyChips — renders a formatted hotkey string as shadcn/ui `<Kbd>`
 * chips. `" / "` separates alternative bindings ("Tab / Shift+Tab");
 * `"+"` separates the keys of a combo ("Ctrl+Alt+V"). This is the
 * single visual primitive for every hotkey display in the app, so the
 * chip styling always matches the design-system `Kbd` component.
 */
export function HotkeyChips({ keys, className }: HotkeyChipsProps) {
	const alternatives = keys.split(" / ");
	return (
		<>
			{alternatives.map((alt, i) => (
				<Fragment key={alt}>
					{i > 0 && (
						<span aria-hidden className="text-xs text-(--text-muted)">
							{" / "}
						</span>
					)}
					<HotkeyCombo keys={alt} className={className} />
				</Fragment>
			))}
		</>
	);
}
