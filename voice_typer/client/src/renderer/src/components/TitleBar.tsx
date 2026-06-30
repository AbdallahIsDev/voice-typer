import { useEffect, useState } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { PanelLeftIcon } from '@hugeicons/core-free-icons'
import { cn } from '@/lib/utils'
import type { WindowBridge } from '@/types/ipc'

interface TitleBarProps {
  onToggleSidebar?: () => void
  onGoBack?: () => void
  onGoForward?: () => void
  canGoBack?: boolean
  canGoForward?: boolean
  // NEW-TS-007: isMaximized is now lifted to App.tsx (single source of
  // truth) and passed down as a prop.  Previously TitleBar maintained
  // its own isMaximized state AND subscribed to bridge.onMaximizedChanged
  // independently — two subscriptions to the same event, potential for
  // state drift, and double the IPC traffic.
  isMaximized?: boolean
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
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="stroke-current fill-none" strokeWidth="1.25">
      <rect x="0.5" y="0.5" width="9" height="9" />
    </svg>
  )
}

function RestoreIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="stroke-current fill-none" strokeWidth="1.25">
      <path d="M3 0.5 H9.5 V7" />
      <rect x="0.5" y="2.5" width="7" height="7" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden className="stroke-current" strokeWidth="1.25">
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

export function TitleBar({ onToggleSidebar, onGoBack, onGoForward, canGoBack, canGoForward, isMaximized: isMaximizedProp }: TitleBarProps) {
  // NEW-TS-007: use the prop from App.tsx when available; fall back to
  // local state for standalone usage (e.g. storybook, tests where the
  // parent doesn't pass isMaximized).  When the prop is provided, we
  // skip the subscription entirely — App.tsx owns the single source of
  // truth.
  const [localIsMaximized, setLocalIsMaximized] = useState(false)
  const bridge = typeof window !== 'undefined' ? window.window_ as WindowBridge : undefined

  useEffect(() => {
    // Only subscribe if the parent didn't pass isMaximized as a prop.
    if (isMaximizedProp !== undefined) return
    if (!bridge) return
    let cancelled = false
    bridge.isMaximized().then((v) => { if (!cancelled) setLocalIsMaximized(v) }).catch(() => { })
    const unsub = bridge.onMaximizedChanged((v) => { if (!cancelled) setLocalIsMaximized(v) })
    return () => { cancelled = true; unsub() }
  }, [bridge, isMaximizedProp])

  const isMaximized = isMaximizedProp !== undefined ? isMaximizedProp : localIsMaximized

  const handleMinimize = () => bridge?.minimize().catch(() => { })
  const handleToggleMaximize = () => bridge?.toggleMaximize().catch(() => { })
  const handleClose = () => bridge?.close().catch(() => { })

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
        <HugeiconsIcon icon={PanelLeftIcon} strokeWidth={2} className="h-4 w-4" />
      </button>

      {/* Back/Forward navigation */}
      <button
        type="button"
        onClick={onGoBack}
        disabled={!canGoBack}
        aria-label="Go back"
        title="Back (or mouse back button)"
        className={cn(
          'no-drag press-scale flex h-8 w-8 items-center justify-center rounded',
          'text-(--text-muted) transition-colors duration-75',
          'hover:bg-black/5 hover:text-(--text-primary)',
          'dark:hover:bg-white/5',
          'disabled:opacity-30 disabled:cursor-not-allowed',
          'focus:outline-none',
        )}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M10 12L6 8L10 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <button
        type="button"
        onClick={onGoForward}
        disabled={!canGoForward}
        aria-label="Go forward"
        title="Forward (or mouse forward button)"
        className={cn(
          'no-drag press-scale flex h-8 w-8 items-center justify-center rounded',
          'text-(--text-muted) transition-colors duration-75',
          'hover:bg-black/5 hover:text-(--text-primary)',
          'dark:hover:bg-white/5',
          'disabled:opacity-30 disabled:cursor-not-allowed',
          'focus:outline-none',
        )}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M6 4L10 8L6 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
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
