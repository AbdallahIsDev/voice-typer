import { useCallback } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import type { IconSvgElement } from "@hugeicons/react"
import {
  Sun01Icon,
  Moon02Icon,
  ModernTvIcon,
} from "@hugeicons/core-free-icons"
import type { VoiceTyperConfig } from '@/types/config'
import { cn } from '@/lib/utils'

const THEME_CYCLE: { mode: VoiceTyperConfig['theme_mode']; icon: IconSvgElement; label: string }[] = [
  { mode: 'light', icon: Sun01Icon, label: 'Light' },
  { mode: 'dark', icon: Moon02Icon, label: 'Dark' },
  { mode: 'system', icon: ModernTvIcon, label: 'System' },
]

/** Get the next mode in the cycle. Light → Dark → System → Light */
function nextMode(current: VoiceTyperConfig['theme_mode']): VoiceTyperConfig['theme_mode'] {
  const idx = THEME_CYCLE.findIndex((t) => t.mode === current)
  return THEME_CYCLE[(idx + 1) % THEME_CYCLE.length].mode
}

interface ThemeSwitchProps {
  themeMode: VoiceTyperConfig['theme_mode']
  onThemeChange: (mode: VoiceTyperConfig['theme_mode']) => void
  collapsed?: boolean
}

export function ThemeSwitch({ themeMode, onThemeChange, collapsed = false }: ThemeSwitchProps) {
  const current = THEME_CYCLE.find((t) => t.mode === themeMode) ?? THEME_CYCLE[0]

  const handleClick = useCallback(() => {
    onThemeChange(nextMode(themeMode))
  }, [themeMode, onThemeChange])

  return (
    <button
      onClick={handleClick}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-full transition-[padding] duration-200 ease-out',
        'hover:bg-black/5 dark:hover:bg-white/10',
        collapsed ? 'h-7 w-7 justify-center gap-0' : 'h-7 px-2.5 gap-2',
      )}
      title={`${current.label} mode — click to switch`}
      aria-label={`Current theme: ${current.label}. Click to switch.`}
    >
      <HugeiconsIcon icon={current.icon} strokeWidth={1.625} className="h-3.5 w-3.5 shrink-0" />
      <span
        className={cn(
          'overflow-hidden whitespace-nowrap text-sm font-medium dark:font-normal',
          'transition-[max-width,opacity,filter] duration-200 ease-out',
          collapsed ? 'max-w-0 opacity-0 filter-[blur(4px)]' : 'max-w-16 opacity-100',
        )}
      >
        {current.label}
      </span>
    </button>
  )
}
