import { HugeiconsIcon } from "@hugeicons/react"
import type { IconSvgElement } from "@hugeicons/react"
import {
  Home03Icon,
  HistoryIcon,
  File02Icon,
  BookOpen02Icon,
  AiBrain03Icon,
  Mic02Icon,
  DashboardSquare01Icon,
  Settings03Icon,
} from "@hugeicons/core-free-icons"
import type { Page } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/Logo'
import { ThemeSwitch } from '@/components/ThemeSwitch'
import { cn } from '@/lib/utils'

interface NavItem {
  id: Page
  label: string
  icon: IconSvgElement
}

const NAV_ITEMS: NavItem[] = [
  { id: 'home', label: 'Home', icon: Home03Icon },
  { id: 'history', label: 'History', icon: HistoryIcon },
  { id: 'templates', label: 'Templates', icon: File02Icon },
  { id: 'vocabulary', label: 'Vocabulary', icon: BookOpen02Icon },
  { id: 'models', label: 'Models', icon: AiBrain03Icon },
  { id: 'microphone', label: 'Microphone', icon: Mic02Icon },
  { id: 'dashboard', label: 'Dashboard',  icon: DashboardSquare01Icon },
  { id: 'settings', label: 'Settings', icon: Settings03Icon },
]

interface SidebarProps {
  currentPage: Page
  onNavigate: (page: Page) => void
  themeMode: VoiceTyperConfig['theme_mode']
  onThemeChange: (mode: VoiceTyperConfig['theme_mode']) => void
  collapsed?: boolean
}

export function Sidebar({ currentPage, onNavigate, themeMode, onThemeChange, collapsed = false }: SidebarProps) {
  return (
    <aside
      className={cn(
        'flex shrink-0 flex-col',
        'overflow-hidden',
        'transition-[width] duration-200 ease-out',
        collapsed ? 'w-12' : 'w-55',
      )}
    >
      {/* Logo + Title */}
      <div
        className={cn(
          'flex shrink-0 items-center border-b border-border',
          collapsed ? 'justify-center px-2 py-4' : 'gap-2.5 px-5 py-5',
        )}
        title={collapsed ? 'Voice Typer' : undefined}
      >
        <Logo size={24} className="shrink-0" />
        {!collapsed && (
          <span className="font-sans text-base font-medium tracking-normal text-(--text-primary)">
            Voice Typer
          </span>
        )}
      </div>

      {/* Navigation */}
      <div className="flex-1 p-3">
        <nav className={cn('bg-(--surface-hover) rounded-lg space-y-0.5 border border-border', collapsed ? 'p-2' : 'p-1')}>
          {NAV_ITEMS.map((item) => {
            const isActive = currentPage === item.id
            return (
              <Button
                key={item.id}
                variant="ghost"
                title={collapsed ? item.label : undefined}
                className={cn(
                  'w-full text-sm tracking-wide normal-case font-normal rounded-md',
                  'transition-colors duration-100',
                  collapsed ? 'justify-center px-0' : 'justify-start gap-3',
                  isActive
                    ? 'bg-white hover:bg-white border border-border dark:bg-(--bg) dark:hover:bg-(--bg) dark:border-black'
                    : 'hover:bg-black/5 dark:hover:bg-white/5',
                )}
                onClick={() => onNavigate(item.id)}
              >
                <HugeiconsIcon icon={item.icon} className="h-4 w-4" />
                {!collapsed && item.label}
              </Button>
            )
          })}
        </nav>
      </div>

      {/* Theme Toggle + Footer */}
      <div className={cn(
        'flex items-center border-t border-border',
        collapsed ? 'justify-center p-3' : 'justify-between p-3',
      )}>
        <ThemeSwitch
          themeMode={themeMode}
          onThemeChange={onThemeChange}
          collapsed={collapsed}
        />
        {!collapsed && (
          <p className="text-[10px] font-mono uppercase tracking-wide text-(--text-muted) opacity-75 text-center leading-none">
            v1.0.0
          </p>
        )}
      </div>
    </aside>
  )
}
