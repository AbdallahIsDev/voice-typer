import { HugeiconsIcon } from "@hugeicons/react"
import type { IconSvgElement } from "@hugeicons/react"
import {
  Home03Icon,
  HistoryIcon,
  File02Icon,
  BookOpen02Icon,
  AiBrain03Icon,
  Mic02Icon,
  Shield01Icon,
  Settings03Icon,
  Sun01Icon,
  Moon01Icon,
  ComputerIcon,
} from "@hugeicons/core-free-icons"
import type { Page } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/Logo'
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
  { id: 'privacy', label: 'Privacy', icon: Shield01Icon },
  { id: 'settings', label: 'Settings', icon: Settings03Icon },
]

interface SidebarProps {
  currentPage: Page
  onNavigate: (page: Page) => void
  themeMode: VoiceTyperConfig['theme_mode']
  onThemeChange: (mode: VoiceTyperConfig['theme_mode']) => void
  collapsed?: boolean
}

const THEME_BUTTONS: { mode: VoiceTyperConfig['theme_mode']; icon: IconSvgElement; label: string }[] = [
  { mode: 'light', icon: Sun01Icon, label: 'Light' },
  { mode: 'system', icon: ComputerIcon, label: 'System' },
  { mode: 'dark', icon: Moon01Icon, label: 'Dark' },
]

export function Sidebar({ currentPage, onNavigate, themeMode, onThemeChange, collapsed = false }: SidebarProps) {
  return (
    <aside
      className={cn(
        'flex shrink-0 flex-col',
        'border-r border-[var(--border)] bg-[var(--bg-subtle)]',
        'transition-[width] duration-200 ease-out',
        collapsed ? 'w-14' : 'w-[220px]',
      )}
    >
      {/* Brand: real project logo + app name, separated from the
          navigation tabs by a divider. When collapsed, only the
          logo icon is shown. */}
      <div
        className={cn(
          'flex shrink-0 items-center',
          collapsed ? 'justify-center px-2 py-4' : 'gap-2.5 px-5 py-5',
        )}
        title={collapsed ? 'Voice Typer' : undefined}
      >
        <Logo size={24} className="shrink-0" />
        {!collapsed && (
          <span className="font-sans text-base font-semibold tracking-tight text-[var(--text-primary)]">
            Voice Typer
          </span>
        )}
      </div>

      {/* Divider between brand and nav tabs */}
      {!collapsed && <div className="mx-4 h-px bg-[var(--border)]" />}

      {/* Navigation */}
      <nav className={cn('flex-1 space-y-0.5', collapsed ? 'p-2' : 'p-3')}>
        {NAV_ITEMS.map((item) => {
          const isActive = currentPage === item.id
          const Icon = item.icon

          return (
            <div
              key={item.id}
              className={cn(
                'border-l-2',
                isActive ? 'border-[var(--accent)]' : 'border-transparent',
              )}
            >
              <Button
                variant="ghost"
                title={collapsed ? item.label : undefined}
                className={cn(
                  'w-full rounded-l-none text-sm tracking-normal normal-case',
                  collapsed ? 'justify-center px-0' : 'justify-start gap-3',
                  isActive
                    ? 'bg-[var(--accent-soft)] text-[var(--accent)] hover:bg-[var(--accent-soft)] hover:text-[var(--accent)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                )}
                onClick={() => onNavigate(item.id)}
              >
                <HugeiconsIcon icon={Icon} className="h-4 w-4" />
                {!collapsed && item.label}
              </Button>
            </div>
          )
        })}
      </nav>

      {/* Theme Toggle */}
      <div
        className={cn(
          'flex shrink-0 items-center justify-center gap-1 border-t border-[var(--border)]',
          collapsed ? 'px-1 py-2' : 'px-4 py-3',
        )}
      >
        {THEME_BUTTONS.map((btn) => (
          <button
            key={btn.mode}
            onClick={() => onThemeChange(btn.mode)}
            className={cn(
              'flex h-7 w-7 items-center justify-center rounded-md transition-all',
              themeMode === btn.mode
                ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--surface-hover)]',
            )}
            title={`${btn.label} mode`}
          >
            <HugeiconsIcon icon={btn.icon} className="h-3.5 w-3.5" />
          </button>
        ))}
      </div>

      {/* Footer */}
      {!collapsed && (
        <div className="shrink-0 px-5 py-3">
          <p className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] opacity-50 text-center">
            v1.0.0
          </p>
        </div>
      )}
    </aside>
  )
}
