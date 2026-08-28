/**
 * Custom sun/moon "auto theme" icon — a 24×24 SVG glyph representing
 * "follow the OS default" (light/dark auto). Used as the system-mode
 * icon in the ThemeSwitch (title bar) and the Settings Appearance
 * SegmentedControl, replacing the previous ModernTvIcon.
 *
 * The icon (source: the standard SunMoon glyph): a sun with rays in
 * the top-right, a crescent moon in the bottom-left.
 *
 * Exported as an `IconSvgElement` (the hugeicons icon data format) so
 * it can be rendered through the shared `HugeiconsIcon` component
 * without any special-case branching.
 */
import type { IconSvgElement } from "@hugeicons/react";

export const SunMoonIcon: IconSvgElement = [
	// Sun — small arc (the visible disc edge)
	[
		"path",
		{
			d: "M16.5 11.5C16.5 9.567 14.933 8 13 8",
			stroke: "currentColor",
			strokeLinecap: "round",
			strokeLinejoin: "round",
			strokeWidth: "1.5",
			key: "0",
		},
	],
	// Sun rays — three short lines around the top-right disc
	[
		"path",
		{
			d: "M13.5 2.5V4.5M19.5018 4.5L18.0028 5.99902M21.5018 10.5H19.502",
			stroke: "currentColor",
			strokeLinecap: "round",
			strokeLinejoin: "round",
			strokeWidth: "1.5",
			key: "1",
		},
	],
	// Moon — crescent in the bottom-left
	[
		"path",
		{
			d: "M17 16.5314C16.116 17.0034 15.1064 17.271 14.0343 17.271C10.552 17.271 7.72899 14.448 7.72899 10.9657C7.72899 9.89358 7.99657 8.88398 8.46857 8C5.33406 8.73462 3 11.548 3 14.9065C3 18.8241 6.17586 22 10.0935 22C13.452 22 16.2654 19.6659 17 16.5314Z",
			stroke: "currentColor",
			strokeLinecap: "round",
			strokeLinejoin: "round",
			strokeWidth: "1.5",
			key: "2",
		},
	],
];
