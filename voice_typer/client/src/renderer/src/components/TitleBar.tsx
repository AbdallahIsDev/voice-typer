import { useEffect, useState } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { PanelLeftIcon } from '@hugeicons/core-free-icons'
import { cn } from '@/lib/utils'
import type { WindowBridge } from '@/types/ipc'

interface TitleBarProps {
  onToggleSidebar?: () => void
}

function MinimizeIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="fill-current">
      <rect x="0" y="8" width="10" height="1" />
    </svg>
  )
}

function MaximizeIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="stroke-current fill-none" strokeWidth="1.625">
      <rect x="0.5" y="0.5" width="9" height="9" />
    </svg>
  )
}

function RestoreIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="stroke-current fill-none" strokeWidth="1.625">
      <path d="M3 0.5 H9.5 V7" />
      <rect x="0.5" y="2.5" width="7" height="7" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="stroke-current" strokeWidth="1.625">
      <line x1="0.5" y1="0.5" x2="9.5" y2="9.5" />
      <line x1="9.5" y1="0.5" x2="0.5" y2="9.5" />
    </svg>
  )
}

interface TitleBarButtonProps {
  onClick: () => void
  ariaLabel: string
  variant?: 'default' | 'close'
  children: React.ReactNode
}

function TitleBarButton({ onClick, ariaLabel, variant = 'default', children }: TitleBarButtonProps) {
  const isClose = variant === 'close'
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      tabIndex={-1}
      className={cn(
        'no-drag press-scale group flex items-center justify-center',
        'h-8 w-11.5',
        'text-(--text-muted) transition-colors duration-75',
        'focus:outline-none',
        isClose
          ? cn(
              'hover:bg-[#C42B1C] hover:text-white',
              'focus-visible:bg-[#C42B1C] focus-visible:text-white',
            )
          : cn(
              'hover:bg-black/5 dark:hover:bg-white/5',
              'hover:text-(--text-primary)',
            ),
      )}
    >
      {children}
    </button>
  )
}

export function TitleBar({ onToggleSidebar }: TitleBarProps) {
  const [isMaximized, setIsMaximized] = useState(false)
  const bridge = typeof window !== 'undefined' ? window.window_ as WindowBridge : undefined

  useEffect(() => {
    if (!bridge) return
    let cancelled = false
    bridge.isMaximized().then((v) => { if (!cancelled) setIsMaximized(v) }).catch(() => {})
    const unsub = bridge.onMaximizedChanged((v) => { if (!cancelled) setIsMaximized(v) })
    return () => { cancelled = true; unsub() }
  }, [bridge])

  const handleMinimize = () => bridge?.minimize().catch(() => {})
  const handleToggleMaximize = () => bridge?.toggleMaximize().catch(() => {})
  const handleClose = () => bridge?.close().catch(() => {})

  return (
    <div className="drag-region flex w-full shrink-0 items-center select-none h-8">
      <button
        type="button"
        onClick={onToggleSidebar}
        aria-label="Toggle sidebar"
        title="Toggle sidebar"
        className={cn(
          'no-drag press-scale flex h-10 w-10 items-center justify-center',
          'text-(--text-muted)',
          'hover:text-(--text-primary)',
          'focus:outline-none',
        )}
      >
        <HugeiconsIcon icon={PanelLeftIcon} strokeWidth={1.5} className="h-4 w-4" />
      </button>
      <div className="flex-1" />

      <TitleBarButton onClick={handleMinimize} ariaLabel="Minimize">
        <MinimizeIcon />
      </TitleBarButton>
      <TitleBarButton
        onClick={handleToggleMaximize}
        ariaLabel={isMaximized ? 'Restore' : 'Maximize'}
      >
        {isMaximized ? <RestoreIcon /> : <MaximizeIcon />}
      </TitleBarButton>
      <TitleBarButton onClick={handleClose} ariaLabel="Close" variant="close">
        <CloseIcon />
      </TitleBarButton>
    </div>
  )
}
