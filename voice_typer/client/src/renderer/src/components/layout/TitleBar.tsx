import { PanelLeftIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { t } from "@/i18n/i18n";
import { cn, focusRing } from "@/lib/utils";
import type { WindowBridge } from "@/types/ipc";

interface TitleBarProps {
	onToggleSidebar?: () => void;
	onGoBack?: () => void;
	onGoForward?: () => void;
	canGoBack?: boolean;
	canGoForward?: boolean;
	// GT-E2-10 (session-6 dedup): ``isMaximized`` is now a REQUIRED prop.
	// Previously TitleBar had its own local ``useState`` +
	// ``onMaximizedChanged`` subscription as a fallback for when the
	// prop was undefined — duplicating the subscription already living
	// in App.tsx (App.tsx:161-189) which always passes the prop. The
	// duplicate subscription is deleted; App.tsx is the single owner of
	// the maximize-state subscription.
	isMaximized: boolean;
	// UX-8: open the keyboard-shortcut help overlay. Previously the
	// overlay was only reachable via the "?" key — invisible to users
	// who never discover that shortcut. Exposing a TitleBar button
	// makes the overlay discoverable for mouse + keyboard users alike.
	onOpenHelp?: () => void;
}

function MinimizeIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden
			className="fill-current"
		>
			<title>{t("titleBar.minimize")}</title>
			<rect x="0" y="8" width="10" height="1" />
		</svg>
	);
}

function MaximizeIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden
			className="stroke-current fill-none"
			strokeWidth="1.25"
		>
			<title>{t("titleBar.maximize")}</title>
			<rect x="0.5" y="0.5" width="9" height="9" />
		</svg>
	);
}

function RestoreIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden
			className="stroke-current fill-none"
			strokeWidth="1.25"
		>
			<title>{t("titleBar.restore")}</title>
			<path d="M3 0.5 H9.5 V7" />
			<rect x="0.5" y="2.5" width="7" height="7" />
		</svg>
	);
}

function CloseIcon() {
	return (
		<svg
			width="10"
			height="10"
			viewBox="0 0 10 10"
			aria-hidden
			className="stroke-current"
			strokeWidth="1.25"
		>
			<title>{t("titleBar.close")}</title>
			<line x1="0.5" y1="0.5" x2="9.5" y2="9.5" />
			<line x1="9.5" y1="0.5" x2="0.5" y2="9.5" />
		</svg>
	);
}

interface TitleBarButtonProps {
	onClick: () => void;
	ariaLabel: string;
	variant?: "default" | "close";
	children: React.ReactNode;
}

function TitleBarButton({
	onClick,
	ariaLabel,
	variant = "default",
	children,
}: TitleBarButtonProps) {
	const isClose = variant === "close";
	return (
		<button
			type="button"
			onClick={onClick}
			aria-label={ariaLabel}
			className={cn(
				"no-drag press-scale group flex items-center justify-center",
				"h-8 w-11.5",
				"text-(--text-muted) transition-colors duration-150",
				// XA-1: shared focusRing (ring-3 / ring-ring/30) so the
				// title-bar focus ring matches the design-system Button's
				// ring thickness instead of being visually thinner (ring-2).
				focusRing,
				isClose
					? cn(
							// PVT-022: replace hardcoded #C42B1C with the
							// destructive design tokens so the close button
							// follows the active theme's destructive palette
							// (and stays readable in dark mode / custom themes).
							"hover:bg-destructive hover:text-destructive-foreground",
							"focus-visible:bg-destructive focus-visible:text-destructive-foreground",
						)
					: // task-9: theme-aware hover (replaces the physical
						// black/white pairing so custom + dark themes get a
						// consistent hover wash).
						cn("hover:bg-foreground/5", "hover:text-(--text-primary)"),
			)}
		>
			{children}
		</button>
	);
}

export function TitleBar({
	onToggleSidebar,
	onGoBack,
	onGoForward,
	canGoBack,
	canGoForward,
	isMaximized,
	onOpenHelp,
}: TitleBarProps) {
	const bridge =
		typeof window !== "undefined"
			? (window.window_ as WindowBridge)
			: undefined;

	const handleMinimize = () =>
		bridge
			?.minimize()
			.catch((err) =>
				console.warn("[IPC] window control failed: minimize:", err),
			);
	const handleToggleMaximize = () =>
		bridge
			?.toggleMaximize()
			.catch((err) =>
				console.warn("[IPC] window control failed: toggleMaximize:", err),
			);
	const handleClose = () =>
		bridge
			?.close()
			.catch((err) => console.warn("[IPC] window control failed: close:", err));

	return (
		<div className="drag-region flex w-full shrink-0 items-center select-none h-8">
			<button
				type="button"
				onClick={onToggleSidebar}
				aria-label={t("a11y.toggleSidebarWithShortcut", { shortcut: "Ctrl+B" })}
				// PVT-023: expose the keyboard shortcut via aria-keyshortcuts
				// so AT users can discover it without inspecting the tooltip.
				// "Control+B" matches the ARIA keyshortcuts spec format
				// (Modifier+Key, case-significant).
				aria-keyshortcuts="Control+B"
				// UX-17: surface the Ctrl+B keyboard shortcut in the
				// tooltip so users discover the keyboard alternative.
				title={t("a11y.toggleSidebarWithShortcut", { shortcut: "Ctrl+B" })}
				className={cn(
					// Fix: sidebar toggle button height matches the TitleBar
					// h-8 so the icon stays vertically centered (was h-10
					// w-10 which made it taller than the 32px title bar).
					"no-drag press-scale flex h-8 w-8 items-center justify-center",
					"text-(--text-muted)",
					// XA-1: parity with sibling back/forward/help buttons —
					// add rounded corners + transition + hover bg so the
					// toggle snaps in consistently with its neighbors
					// (previously the only TitleBar button without a hover bg).
					"rounded transition-colors duration-150",
					"hover:bg-foreground/5 hover:text-(--text-primary)",
					focusRing,
				)}
			>
				<HugeiconsIcon
					icon={PanelLeftIcon}
					strokeWidth={2}
					className="h-4 w-4"
				/>
			</button>

			{/* Back/Forward navigation */}
			<button
				type="button"
				onClick={onGoBack}
				disabled={!canGoBack}
				aria-label={t("a11y.goBack")}
				// NF-R10-10: surface Alt+Left shortcut in the tooltip.
				title={t("titleBar.backWithShortcut", { shortcut: "Alt+←" })}
				className={cn(
					"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
					"text-(--text-muted) transition-colors duration-150",
					// task-9: theme-aware hover (replaces the physical
					// black/white pairing so custom + dark themes get a
					// consistent hover wash).
					"hover:bg-foreground/5 hover:text-(--text-primary)",
					"disabled:opacity-30 disabled:cursor-not-allowed",
					focusRing,
				)}
			>
				<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
					<title>{t("titleBar.back")}</title>
					<path
						d="M10 12L6 8L10 4"
						stroke="currentColor"
						strokeWidth="1.5"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
				</svg>
			</button>
			<button
				type="button"
				onClick={onGoForward}
				disabled={!canGoForward}
				aria-label={t("a11y.goForward")}
				// NF-R10-10: surface Alt+Right shortcut in the tooltip.
				title={t("titleBar.forwardWithShortcut", { shortcut: "Alt+→" })}
				className={cn(
					"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
					"text-(--text-muted) transition-colors duration-150",
					"hover:bg-foreground/5 hover:text-(--text-primary)",
					"disabled:opacity-30 disabled:cursor-not-allowed",
					focusRing,
				)}
			>
				<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
					<title>{t("titleBar.forward")}</title>
					<path
						d="M6 4L10 8L6 12"
						stroke="currentColor"
						strokeWidth="1.5"
						strokeLinecap="round"
						strokeLinejoin="round"
					/>
				</svg>
			</button>

			{/* UX-8: discoverable "?" help button. Mirrors the "?"
			    keyboard shortcut (handled in App.tsx) so mouse users and
			    AT users can also open the keyboard-shortcut overlay. */}
			<button
				type="button"
				onClick={onOpenHelp}
				aria-label={t("help.openHelp")}
				// PVT-023: expose the "?" shortcut via aria-keyshortcuts so
				// AT users can discover that pressing "?" opens this overlay.
				aria-keyshortcuts="?"
				title={t("help.openHelp")}
				className={cn(
					"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
					"text-(--text-muted) transition-colors duration-150",
					"hover:bg-foreground/5 hover:text-(--text-primary)",
					focusRing,
				)}
			>
				<span aria-hidden className="text-[13px] font-semibold leading-none">
					?
				</span>
			</button>

			<div className="flex-1" />

			<TitleBarButton
				onClick={handleMinimize}
				ariaLabel={t("titleBar.minimize")}
			>
				<MinimizeIcon />
			</TitleBarButton>
			<TitleBarButton
				onClick={handleToggleMaximize}
				ariaLabel={isMaximized ? t("titleBar.restore") : t("titleBar.maximize")}
			>
				{isMaximized ? <RestoreIcon /> : <MaximizeIcon />}
			</TitleBarButton>
			<TitleBarButton
				onClick={handleClose}
				ariaLabel={t("titleBar.close")}
				variant="close"
			>
				<CloseIcon />
			</TitleBarButton>
		</div>
	);
}
