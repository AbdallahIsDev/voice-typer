import { PanelLeftIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState } from "react";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { WindowBridge } from "@/types/ipc";

interface TitleBarProps {
	onToggleSidebar?: () => void;
	onGoBack?: () => void;
	onGoForward?: () => void;
	canGoBack?: boolean;
	canGoForward?: boolean;
	isMaximized?: boolean;
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
				"text-(--text-muted) transition-colors duration-75",
				"focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none",
				isClose
					? cn(
							"hover:bg-[#C42B1C] hover:text-white",
							"focus-visible:bg-[#C42B1C] focus-visible:text-white",
						)
					: cn(
							"hover:bg-black/5 dark:hover:bg-white/5",
							"hover:text-(--text-primary)",
						),
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
	isMaximized: isMaximizedProp,
	onOpenHelp,
}: TitleBarProps) {
	const [localIsMaximized, setLocalIsMaximized] = useState(false);
	const bridge =
		typeof window !== "undefined"
			? (window.window_ as WindowBridge)
			: undefined;

	useEffect(() => {
		if (isMaximizedProp !== undefined) return;
		if (!bridge) return;
		let cancelled = false;
		bridge
			.isMaximized()
			.then((v) => {
				if (!cancelled) setLocalIsMaximized(v);
			})
			.catch(() => {});
		const unsub = bridge.onMaximizedChanged((v) => {
			if (!cancelled) setLocalIsMaximized(v);
		});
		return () => {
			cancelled = true;
			unsub();
		};
	}, [bridge, isMaximizedProp]);

	const isMaximized =
		isMaximizedProp !== undefined ? isMaximizedProp : localIsMaximized;

	const handleMinimize = () => bridge?.minimize().catch(() => {});
	const handleToggleMaximize = () => bridge?.toggleMaximize().catch(() => {});
	const handleClose = () => bridge?.close().catch(() => {});

	return (
		<div className="drag-region flex w-full shrink-0 items-center select-none h-8">
			<button
				type="button"
				onClick={onToggleSidebar}
				aria-label={t("a11y.toggleSidebar")}
				title={t("a11y.toggleSidebar")}
				className={cn(
					"no-drag press-scale flex h-10 w-10 items-center justify-center",
					"text-(--text-muted)",
					"hover:text-(--text-primary)",
					"focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none",
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
				title={t("titleBar.back")}
				className={cn(
					"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
					"text-(--text-muted) transition-colors duration-75",
					"hover:bg-black/5 hover:text-(--text-primary)",
					"dark:hover:bg-white/5",
					"disabled:opacity-30 disabled:cursor-not-allowed",
					"focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none",
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
				title={t("titleBar.forward")}
				className={cn(
					"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
					"text-(--text-muted) transition-colors duration-75",
					"hover:bg-black/5 hover:text-(--text-primary)",
					"dark:hover:bg-white/5",
					"disabled:opacity-30 disabled:cursor-not-allowed",
					"focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none",
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
				title={t("help.openHelp")}
				className={cn(
					"no-drag press-scale flex h-8 w-8 items-center justify-center rounded",
					"text-(--text-muted) transition-colors duration-75",
					"hover:bg-black/5 hover:text-(--text-primary)",
					"dark:hover:bg-white/5",
					"focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none",
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
