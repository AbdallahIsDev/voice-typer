import { HugeiconsIcon } from '@hugeicons/react'
import type { IconSvgElement } from "@hugeicons/react"
import {
  Sun01Icon,
  Moon02Icon,
  ModernTvIcon,
} from "@hugeicons/core-free-icons"
import type { VoiceTyperConfig } from '@/types/config'
import { cn } from '@/lib/utils'

const THEME_BUTTONS: { mode: VoiceTyperConfig['theme_mode']; icon: IconSvgElement; label: string }[] = [
  { mode: 'light', icon: Sun01Icon, label: 'Light' },
  { mode: 'system', icon: ModernTvIcon, label: 'System' },
  { mode: 'dark', icon: Moon02Icon, label: 'Dark' },
]

interface ThemeSwitchProps {
  themeMode: VoiceTyperConfig['theme_mode']
  onThemeChange: (mode: VoiceTyperConfig['theme_mode']) => void
  collapsed?: boolean
}

export function ThemeSwitch({ themeMode, onThemeChange, collapsed = false }: ThemeSwitchProps) {
  return (
    <div
      className={cn(
        'flex w-fit gap-1 bg-(--surface-hover) p-0.5 rounded-full border border-border',
        collapsed ? 'flex-col' : 'items-center justify-center',
      )}
    >
      {THEME_BUTTONS.map((btn) => (
        <button
          key={btn.mode}
          onClick={() => onThemeChange(btn.mode)}
          className={cn(
            'h-7 w-7 duration-0 rounded-full flex items-center justify-center',
            themeMode === btn.mode
              ? 'bg-(--bg)'
              : 'text-(--text-muted) hover:text-(--text-primary) hover:bg-(--surface-hover)',
          )}
          title={`${btn.label} mode`}
        >
          <HugeiconsIcon icon={btn.icon} strokeWidth={1.625} className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  )
}
