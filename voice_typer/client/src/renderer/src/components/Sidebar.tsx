import { HugeiconsIcon } from '@hugeicons/react'
import type { IconSvgElement } from "@hugeicons/react"
import {
  Home04Icon,
  HistoryIcon,
  File02Icon,
  BookOpen02Icon,
  AiBrain03Icon,
  Mic02Icon,
  Analytics01Icon,
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
  { id: 'home', label: 'Home', icon: Home04Icon },
  { id: 'history', label: 'History', icon: HistoryIcon },
  { id: 'analytics', label: 'Analytics',  icon: Analytics01Icon },
  { id: 'templates', label: 'Templates', icon: File02Icon },
  { id: 'vocabulary', label: 'Vocabulary', icon: BookOpen02Icon },
  { id: 'models', label: 'Models', icon: AiBrain03Icon },
  { id: 'microphone', label: 'Microphone', icon: Mic02Icon },
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
          'flex shrink-0 items-center gap-2.5 border-b border-border',
          collapsed ? 'px-3 py-4' : 'px-5 py-5',
        )}
        title={collapsed ? 'Voice Typer' : undefined}
      >
        <Logo size={24} className="shrink-0" />
        <span
          className={cn(
            'overflow-hidden whitespace-nowrap text-base font-medium tracking-normal text-(--text-primary)',
            'transition-all duration-200 ease-out',
            collapsed ? 'max-w-0 opacity-0' : 'max-w-32 opacity-100',
          )}
        >
          Voice Typer
        </span>
      </div>

      {/* Navigation */}
      <div className="flex-1 p-3">
        <nav className="flex flex-col gap-px">
          {NAV_ITEMS.map((item) => {
            const isActive = currentPage === item.id
            return (
              <Button
                key={item.id}
                variant="ghost"
                title={collapsed ? item.label : undefined}
                className={cn(
                  'w-full justify-start gap-3 text-sm tracking-wide normal-case font-normal rounded-md',
                  'transition-all duration-200 ease-out',
                  collapsed ? 'px-0' : 'px-3',
                  isActive
                    ? 'bg-white hover:bg-white border border-border dark:bg-(--bg) dark:hover:bg-(--bg) dark:border-black'
                    : 'hover:bg-black/5 dark:hover:bg-white/5',
                )}
                onClick={() => onNavigate(item.id)}
              >
                <HugeiconsIcon icon={item.icon} strokeWidth={1.625} className="h-4.5 w-4.5 shrink-0" />
                <span
                  className={cn(
                    'overflow-hidden whitespace-nowrap',
                    'transition-all duration-200 ease-out',
                    collapsed ? 'max-w-0 opacity-0' : 'max-w-40 opacity-100',
                  )}
                >
                  {item.label}
                </span>
              </Button>
            )
          })}
        </nav>
      </div>

      {/* Theme Toggle + Footer */}
      <div
        className={cn(
          'flex items-center border-t border-border p-3',
          collapsed ? 'gap-2' : 'justify-between',
        )}
      >
        <ThemeSwitch
          themeMode={themeMode}
          onThemeChange={onThemeChange}
          collapsed={collapsed}
        />
        <span
          className={cn(
            'text-[10px] font-mono uppercase tracking-wide text-(--text-muted) opacity-75 leading-none',
            'overflow-hidden whitespace-nowrap transition-all duration-200 ease-out',
            collapsed ? 'max-w-0 opacity-0' : 'max-w-20 opacity-100',
          )}
        >
          v1.0.0
        </span>
      </div>
    </aside>
  )
}
