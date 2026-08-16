import type { ElementType, ReactNode } from "react";
import { cn } from "@/lib/utils";

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
 * render keyboard shortcuts (HelpOverlay) and voice-inserted
 * characters (PunctuationCheatSheet). The two call sites previously
 * duplicated the same Tailwind class string; centralising it here
 * keeps the visual treatment in sync if the design tokens ever change.
 *
 * Renders `<kbd>` by default. Pass `as="code"` when the content is a
 * voice-inserted character rather than a physical key — `<kbd>` would
 * incorrectly imply the user pressed a key.
 */
export function Kbd({ children, as: Tag = "kbd", className }: KbdProps) {
	return (
		<Tag
			className={cn(
				"rounded border border-border/10 bg-(--bg-subtle) px-2 py-0.5 font-mono text-xs text-(--text-primary)",
				className,
			)}
		>
			{children}
		</Tag>
	);
}
