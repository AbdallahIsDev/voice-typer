import type { ComponentProps, ElementType, ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * Kbd — shared visual primitive for the bordered mono "chip" used to
 * render keyboard shortcuts and voice-inserted characters. The single
 * source of truth for keycap presentation across the entire app.
 *
 * ``KBD_CHIP_CLASSES`` is the raw chip surface (rounded, 1px
 * ``border-border/5`` frame, ``--bg-subtle``, mono text). ``Kbd`` uses
 * it directly; interactive chip-like surfaces (e.g. the tappable
 * template-variable chips) compose it with their own hover/focus state
 * so they render with the exact same visual language as every keycap.
 */
export const KBD_CHIP_CLASSES =
	"rounded border border-border/15 bg-(--bg-subtle) px-2 py-1 font-mono text-xs text-(--text-primary)";

interface KbdProps {
	children: ReactNode;
	/**
	 * The underlying element to render. Defaults to `<kbd>` for keyboard
	 * shortcuts. Pass `"code"` for voice-inserted characters (where
	 * `<kbd>` would imply a physical key press — see PunctuationCheatSheet).
	 */
	as?: ElementType;
	/** Optional className override / extension. */
	className?: string;
}

/**
 * Kbd — shared visual primitive for the bordered mono "chip" used to
 * render keyboard shortcuts and voice-inserted characters. The single
 * source of truth for keycap presentation across the entire app.
 *
 * Renders `<kbd>` by default. Pass `as="code"` when the content is a
 * voice-inserted character rather than a physical key — `<kbd>` would
 * incorrectly imply the user pressed a key.
 */
export function Kbd({ children, as: Tag = "kbd", className }: KbdProps) {
	return (
		<Tag
			data-slot="kbd"
			className={cn(
				`${KBD_CHIP_CLASSES} in-data-[slot=tooltip-content]:bg-foreground/10 in-data-[slot=tooltip-content]:text-foreground`,
				className,
			)}
		>
			{children}
		</Tag>
	);
}

/**
 * KbdGroup — renders a set of `Kbd` chips as adjacent keycaps separated
 * only by a small gap (never a visible `+`). Used by HotkeyChips to lay
 * out the individual keys of a shortcut combo.
 */
export function KbdGroup({ className, ...props }: ComponentProps<"kbd">) {
	return (
		<kbd
			data-slot="kbd-group"
			className={cn("inline-flex items-center gap-1", className)}
			{...props}
		/>
	);
}
