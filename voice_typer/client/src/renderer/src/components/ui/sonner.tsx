"use client";

import {
	Alert02Icon,
	CheckmarkCircle02Icon,
	InformationCircleIcon,
	Loading03Icon,
	MultiplicationSignCircleIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState, useSyncExternalStore } from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";
import { getLocale, isRtlLocale, subscribeLocale } from "@/i18n/i18n";

/**
 * Resolve the current theme for the Sonner toaster.
 *
 * THEME-FIX: previously this used `useTheme()` from `next-themes`, but
 * `next-themes` requires a `<ThemeProvider>` ancestor to actually track
 * theme state — and no such provider is mounted anywhere in the
 * renderer (the app uses a custom `useTheme` hook in `@/hooks/useTheme`
 * that toggles the `dark` class on `<html>`). Without a provider,
 * `next-themes`'s `useTheme()` always returned `{ theme: undefined }`,
 * so Sonner fell back to `theme = "system"` and used the OS-level
 * `prefers-color-scheme` instead of the user's explicit Settings
 * choice. If the user picked "light" on a dark-OS machine, the toaster
 * rendered dark while the rest of the app rendered light.
 *
 * The fix reads the resolved theme directly from the DOM by observing
 * the `dark` class on `document.documentElement` (which `useTheme.ts`
 * toggles). This always matches the app's actual rendered theme,
 * regardless of whether the user chose "system", "light", or "dark".
 */
function useResolvedTheme(): "light" | "dark" {
	const [resolved, setResolved] = useState<"light" | "dark">(() => {
		if (typeof document === "undefined") return "light";
		return document.documentElement.classList.contains("dark")
			? "dark"
			: "light";
	});

	useEffect(() => {
		if (typeof document === "undefined") return;
		const root = document.documentElement;
		// Initial sync.
		setResolved(root.classList.contains("dark") ? "dark" : "light");
		// Watch for class changes (useTheme.ts toggles `dark` on every
		// theme change). MutationObserver is the standard pattern for
		// watching class attribute changes.
		const observer = new MutationObserver(() => {
			setResolved(root.classList.contains("dark") ? "dark" : "light");
		});
		observer.observe(root, {
			attributes: true,
			attributeFilter: ["class"],
		});
		return () => observer.disconnect();
	}, []);

	return resolved;
}

/**
 * React to locale changes live.
 *
 * Subscribes via the i18n module's locale-subscriber registry (the same
 * notification stream that drives `useT()`, fired by `setLocale` /
 * `ensureLocaleLoaded`). The snapshot is a primitive boolean, so
 * `useSyncExternalStore` re-renders the Toaster only when the RTL-ness
 * of the active locale actually flips — e.g. switching English →
 * Arabic moves the toaster corner without a page reload.
 */
function useRtlLocale(): boolean {
	return useSyncExternalStore(
		subscribeLocale,
		() => isRtlLocale(getLocale()),
		() => false,
	);
}

const Toaster = ({ ...props }: ToasterProps) => {
	const theme = useResolvedTheme();
	// Position follows the ACTIVE locale on every change: bottom-right
	// in LTR locales, bottom-left in RTL locales (Arabic) so the toaster
	// sits in the visually-far corner from the reading start.
	const rtl = useRtlLocale();

	return (
		<Sonner
			theme={theme}
			//pin a canonical configuration so every toast
			// looks the same regardless of where it was raised.
			//   - richColors: sonner's semantic palette (green for
			//     success, red for error, amber for warning, blue
			//     for info) layered on top of our CSS-variable
			//     tokens — gives toasts an at-a-glance type signal
			//     without us hand-tinting each variant.
			//   - closeButton: lets users dismiss a sticky toast
			//     (errors stay 8s; some users want them gone now)
			//     without waiting for the timer or hunting for the
			//     action button.
			//   - position: reactive to the active locale (see
			//     useRtlLocale above) — flipping to Arabic at runtime
			//     re-renders the Toaster with the mirrored corner.
			//   - duration={4000}: fallback for toasts raised via
			//     ``toast.*`` directly (bypassing ``useSnackbar``).
			//     The hook applies its own per-type durations
			//     (success=3000, info=4000, warning=6000, error=8000)
			//     which override this default.
			richColors
			closeButton
			position={rtl ? "bottom-left" : "bottom-right"}
			duration={4000}
			visibleToasts={6}
			expand={false}
			className="toaster group"
			icons={{
				success: (
					<HugeiconsIcon
						icon={CheckmarkCircle02Icon}
						strokeWidth={1.625}
						className="size-4"
					/>
				),
				info: (
					<HugeiconsIcon
						icon={InformationCircleIcon}
						strokeWidth={1.625}
						className="size-4"
					/>
				),
				warning: (
					<HugeiconsIcon
						icon={Alert02Icon}
						strokeWidth={1.625}
						className="size-4"
					/>
				),
				error: (
					<HugeiconsIcon
						icon={MultiplicationSignCircleIcon}
						strokeWidth={1.625}
						className="size-4"
					/>
				),
				loading: (
					<HugeiconsIcon
						icon={Loading03Icon}
						strokeWidth={1.625}
						className="size-4 animate-spin"
					/>
				),
			}}
			style={
				{
					"--normal-bg": "var(--popover)",
					"--normal-text": "var(--popover-foreground)",
					"--normal-border": "var(--border)",
					"--border-radius": "var(--radius)",
				} as React.CSSProperties
			}
			{...props}
		/>
	);
};

export { Toaster };
