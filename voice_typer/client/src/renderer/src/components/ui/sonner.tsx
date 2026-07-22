"use client";

import {
	Alert02Icon,
	CheckmarkCircle02Icon,
	InformationCircleIcon,
	Loading03Icon,
	MultiplicationSignCircleIcon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState } from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

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

const Toaster = ({ ...props }: ToasterProps) => {
	const theme = useResolvedTheme();

	return (
		<Sonner
			theme={theme}
			// PVT-027: pin a canonical configuration so every toast
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
			//   - position="bottom-right": matches the OS notification
			//     corner on every desktop OS we ship (Windows
			//     bottom-right, macOS top-right but bottom-right is
			//     the de-facto Electron convention and avoids the
			//     title bar).
			//   - duration={4000}: fallback for toasts raised via
			//     ``toast.*`` directly (bypassing ``useSnackbar``).
			//     The hook applies its own per-type durations
			//     (success=3000, info=4000, warning=6000, error=8000)
			//     which override this default.
			richColors
			closeButton
			position="bottom-right"
			duration={4000}
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
