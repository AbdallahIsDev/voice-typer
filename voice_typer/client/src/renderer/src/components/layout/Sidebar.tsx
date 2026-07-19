import {
	AiBrain03Icon,
	Analytics01Icon,
	BookOpen02Icon,
	File02Icon,
	HistoryIcon,
	Home04Icon,
	InformationCircleIcon,
	Mic02Icon,
	Settings03Icon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import { APP_NAME } from "@/branding";
import { Logo } from "@/components/layout/Logo";
import { ThemeSwitch } from "@/components/layout/ThemeSwitch";
import { Button } from "@/components/ui/button";
import { t } from "@/i18n/i18n";
import { cn } from "@/lib/utils";
import type { VoiceTyperConfig } from "@/types/config";
import type { Page } from "@/types/ipc";

interface NavItem {
	id: Page;
	icon: IconSvgElement;
}

const NAV_ITEMS: NavItem[] = [
	{ id: "home", icon: Home04Icon },
	{ id: "history", icon: HistoryIcon },
	{ id: "analytics", icon: Analytics01Icon },
	{ id: "templates", icon: File02Icon },
	{ id: "vocabulary", icon: BookOpen02Icon },
	{ id: "models", icon: AiBrain03Icon },
	{ id: "microphone", icon: Mic02Icon },
	{ id: "settings", icon: Settings03Icon },
	// NEW-UX-009: About/Diagnostics page with version, config info, privacy, help.
	{ id: "about", icon: InformationCircleIcon },
];

// NF-R10-9: Per-page keyboard shortcuts surfaced in the nav item's
// `title` tooltip so they're discoverable for both collapsed and
// expanded sidebar states. Pages without a shortcut are omitted from
// the map (no suffix appended to the tooltip).
const NAV_SHORTCUTS: Partial<Record<Page, string>> = {
	home: "Ctrl+H",
	settings: "Ctrl+,",
};

interface SidebarProps {
	currentPage: Page;
	onNavigate: (page: Page) => void;
	themeMode: VoiceTyperConfig["theme_mode"];
	onThemeChange: (mode: VoiceTyperConfig["theme_mode"]) => void;
	collapsed?: boolean;
}

export function Sidebar({
	currentPage,
	onNavigate,
	themeMode,
	onThemeChange,
	collapsed = false,
}: SidebarProps) {
	return (
		<aside
			className={cn(
				"flex shrink-0 flex-col",
				"overflow-hidden",
				"transition-[width] duration-200 ease-out",
				collapsed ? "w-12" : "w-55",
			)}
		>
			{/* Logo + Title */}
			<div
				className={cn(
					"flex shrink-0 items-center gap-2.5",
					"transition-[padding] duration-200 ease-out",
					collapsed ? "px-3 py-4" : "px-5 py-4",
				)}
				title={collapsed ? APP_NAME : undefined}
			>
				<Logo size={24} className="shrink-0" />
				<span
					className={cn(
						"overflow-hidden whitespace-nowrap text-base font-medium tracking-normal text-(--text-primary)",
						"transition-all duration-200 ease-out",
						collapsed
							? "max-w-0 opacity-0 filter-[blur(4px)]"
							: "max-w-32 opacity-100 filter-none",
					)}
				>
					{APP_NAME}
				</span>
			</div>

			{/* Navigation */}
			<div className="flex-1 p-2">
				<nav
					aria-label={t("a11y.mainNavigation")}
					className={cn("flex flex-col gap-px")}
				>
					{NAV_ITEMS.map((item) => {
						const isActive = currentPage === item.id;
						const handleNav = () => onNavigate(item.id);
						// NF-R10-9: Always set a `title` (regardless of collapsed
						// state) so keyboard users hovering focus can read the
						// nav label, and include the shortcut when one exists.
						const shortcut = NAV_SHORTCUTS[item.id];
						const navLabel = t(`nav.${item.id}`);
						const navTitle = shortcut ? `${navLabel} (${shortcut})` : navLabel;
						return (
							<Button
								key={item.id}
								variant="ghost"
								title={navTitle}
								// NEW-A11Y-003: aria-current="page" tells screen readers
								// which nav item represents the current page.
								aria-current={isActive ? "page" : undefined}
								className={cn(
									"w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md",
									"transition-all duration-200 ease-out",
									collapsed ? "px-2" : "px-3",
									isActive
										? cn(
												"bg-(--bg) hover:bg-(--bg) border border-border dark:bg-(--bg) dark:hover:bg-(--bg)",
												// UX-16: 2px left accent bar gives the active nav
												// item stronger visual hierarchy (matches VS Code's
												// active-tab indicator).
												"relative before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:h-5 before:w-0.5 before:rounded-full before:bg-accent",
											)
										: "hover:bg-black/5 dark:hover:bg-white/5",
								)}
								onClick={handleNav}
							>
								<HugeiconsIcon
									icon={item.icon}
									strokeWidth={2}
									className="h-4 w-4 shrink-0"
								/>
								<span
									className={cn(
										"overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal",
										"transition-all duration-200 ease-out",
										collapsed
											? "max-w-0 opacity-0 filter-[blur(4px)]"
											: "max-w-40 opacity-100 filter-none",
									)}
								>
									{t(`nav.${item.id}`)}
								</span>
							</Button>
						);
					})}
				</nav>
			</div>

			{/* Theme Toggle */}
			<div className="flex justify-start items-center p-2">
				<ThemeSwitch
					themeMode={themeMode}
					onThemeChange={onThemeChange}
					collapsed={collapsed}
				/>
			</div>
		</aside>
	);
}
