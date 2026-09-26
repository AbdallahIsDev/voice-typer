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
import { getLocale, isRtlLocale, subscribeLocale, t } from "@/i18n/i18n";

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
			// Pin a canonical configuration so every toast
			// looks the same regardless of where it was raised.
			//   - Neutral popover surface: toasts render on the
			//     popover tokens (see the --normal-* style vars
			//     below); the per-type signal comes from the icon
			//     color only (see the .toaster overrides in
			//     index.css).
			//   - closeButton: lets users dismiss a sticky toast
			//     (errors stay 8s; some users want them gone now)
			//     without waiting for the timer or hunting for the
			//     action button.
			//   - position: reactive to the active locale (see
			//     useRtlLocale above), flipping to Arabic at runtime
			//     re-renders the Toaster with the mirrored corner.
			//   - duration={4000}: fallback for toasts raised via
			//     ``toast.*`` directly (bypassing ``useSnackbar``).
			//     The hook applies its own per-type durations
			//     (success=3000, info=4000, warning=6000, error=8000)
			//     which override this default.
			closeButton
			position={rtl ? "bottom-left" : "bottom-right"}
			// Localized accessible names for the toast container and its
			// close button, sonner's built-ins are hard-coded English
			// ("Notifications" / "Close"), which leaked untranslated text
			// to screen-reader users in every non-English locale.
			containerAriaLabel={t("a11y.notifications")}
			toastOptions={{
				closeButtonAriaLabel: t("a11y.close"),
			}}
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
					"--normal-bg": "var(--surface)",
					"--normal-text": "var(--foreground)",
					"--normal-border": "var(--border)",
					"--border-radius": "var(--radius)",
				} as React.CSSProperties
			}
			{...props}
		/>
	);
};

export { Toaster };
