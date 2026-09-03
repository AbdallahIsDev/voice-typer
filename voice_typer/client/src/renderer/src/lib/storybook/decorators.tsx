import type { Decorator } from "@storybook/react";

/**
 * Shared Storybook preview decorator used by every *.stories.tsx file.
 *
 * It mirrors the app's theme contract WITHOUT mutating the global
 * document root, so all stories on an autodocs page stay independent:
 *
 * - Dark mode: the app toggles the `dark` class on
 *   `document.documentElement` (see hooks/useTheme.ts) and
 *   `index.css` defines every theme token under the plain `.dark`
 *   class selector. Applying the same class on a scoped wrapper
 *   therefore re-uses the real production stylesheet — no duplicated
 *   dark palette — and Tailwind's `@custom-variant dark (&:is(.dark *))`
 *   activates for the wrapped subtree.
 *
 * - RTL: the app sets `document.documentElement.dir = "rtl"` AND
 *   `lang = "ar"` for RTL locales (see i18n/store.ts; Arabic is the
 *   only RTL locale). Setting `dir="rtl"` + `lang="ar"` on a scoped
 *   wrapper exercises the same logical-property + `[dir="rtl"]`
 *   descendant-selector behaviour.
 *
 * Radix portals (e.g. InfoTooltip's popup) render at document.body,
 * outside the wrapper, so portal content stays light/LTR — a known,
 * accepted limitation of scoped previews.
 */
export function themeVariantDecorator(options: {
	dark?: boolean;
	rtl?: boolean;
}): Decorator {
	return (Story) => (
		<div
			dir={options.rtl ? "rtl" : undefined}
			lang={options.rtl ? "ar" : undefined}
			className={`bg-background p-6 text-(--text-primary) ${
				options.dark ? "dark" : ""
			}`}
		>
			<Story />
		</div>
	);
}
