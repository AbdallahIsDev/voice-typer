// src/renderer/src/components/Sidebar.tsx

import { HugeiconsIcon } from "@hugeicons/react"
import { Home03Icon, HistoryIcon, Settings03Icon, Mic02Icon } from "@hugeicons/core-free-icons"
import type { Page } from '@/types/ipc'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface NavItem {
  id: Page
  label: string
  icon: React.ElementType
}

const NAV_ITEMS: NavItem[] = [
  { id: 'home', label: 'Home', icon: Home03Icon },
  { id: 'history', label: 'History', icon: HistoryIcon },
  { id: 'settings', label: 'Settings', icon: Settings03Icon },
]

interface SidebarProps {
  currentPage: Page
  onNavigate: (page: Page) => void
}

export function Sidebar({ currentPage, onNavigate }: SidebarProps) {
  return (
    <aside
      className={cn(
        'flex w-[220px] shrink-0 flex-col',
        'border-r border-[var(--border)] bg-[var(--bg-subtle)]',
      )}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div
          className={cn(
            'flex h-8 w-8 items-center justify-center rounded-lg',
            'bg-[var(--accent-soft)] text-[var(--accent)]',
          )}
        >
          <HugeiconsIcon icon={Mic02Icon} className="h-4 w-4" />
        </div>
        <span className="font-serif text-base font-semibold tracking-tight text-[var(--text-primary)]">
          Voice Typer
        </span>
      </div>

      {/* Divider */}
      <div className="mx-4 h-px bg-[var(--border)]" />

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 p-3">
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
                className={cn(
                  'w-full justify-start gap-3 rounded-l-none text-sm',
                  isActive
                    ? 'bg-[var(--accent-soft)] text-[var(--accent)] hover:bg-[var(--accent-soft)] hover:text-[var(--accent)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                )}
                onClick={() => onNavigate(item.id)}
              >
                <HugeiconsIcon icon={Icon} className="h-4 w-4" />
                {item.label}
              </Button>
            </div>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4">
        <p className="text-[10px] font-mono uppercase tracking-widest text-[var(--text-muted)] opacity-50">
          v1.0.0
        </p>
      </div>
    </aside>
  )
}
