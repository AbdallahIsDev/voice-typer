import { useEffect, useState } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { PanelLeftIcon } from '@hugeicons/core-free-icons'
import { cn } from '@/lib/utils'

interface TitleBarProps {
  onToggleSidebar?: () => void
}

function MinimizeIcon() {
  // Windows 11 minimize glyph: a short bar near the bottom of the icon box.
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden
      className="fill-current"
    >
      <rect x="0" y="8" width="10" height="1" />
    </svg>
  )
}

function MaximizeIcon() {
  // Windows 11 maximize glyph: a single rectangle outline.
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden
      className="stroke-current fill-none"
      strokeWidth="1"
    >
      <rect x="0.5" y="0.5" width="9" height="9" />
    </svg>
  )
}

function RestoreIcon() {
  // Windows 11 restore glyph: two overlapping rectangle outlines.
  // The back rectangle peeks from the top-right; the front rectangle sits
  // in the bottom-left, partially covering it.
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden
      className="stroke-current fill-none"
      strokeWidth="1"
    >
      {/* Back rectangle (top-right) */}
      <path d="M3 0.5 H9.5 V7" />
      {/* Front rectangle (bottom-left) */}
      <rect x="0.5" y="2.5" width="7" height="7" />
    </svg>
  )
}

function CloseIcon() {
  // Windows 11 close glyph: two crossing diagonal lines forming an X.
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      aria-hidden
      className="stroke-current"
      strokeWidth="1"
    >
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
        // -webkit-app-region: no-drag lets the button stay clickable
        // while the parent title bar remains a drag region.
        'no-drag press-scale group flex items-center justify-center',
        'h-8 w-[46px]',
        'text-[var(--text-muted)] transition-colors duration-75',
        'focus:outline-none',
        isClose
          ? cn(
              'hover:bg-[#C42B1C] hover:text-white',
              'focus-visible:bg-[#C42B1C] focus-visible:text-white',
            )
          : cn(
              'hover:bg-black/[0.06] dark:hover:bg-white/[0.10]',
              'hover:text-[var(--text-primary)]',
            ),
      )}
    >
      {children}
    </button>
  )
}

export function TitleBar({ onToggleSidebar }: TitleBarProps) {
  const [isMaximized, setIsMaximized] = useState(false)
  const bridge = typeof window !== 'undefined' ? window.window_ : undefined

  useEffect(() => {
    if (!bridge) return
    let cancelled = false
    bridge
      .isMaximized()
      .then((v) => {
        if (!cancelled) setIsMaximized(v)
      })
      .catch(() => {})
    const unsubscribe = bridge.onMaximizedChanged((v) => {
      if (!cancelled) setIsMaximized(v)
    })
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [bridge])

  const handleMinimize = () => {
    bridge?.minimize().catch(() => {})
  }

  const handleToggleMaximize = () => {
    bridge?.toggleMaximize().catch(() => {})
  }

  const handleClose = () => {
    bridge?.close().catch(() => {})
  }

  return (
    <div
      // The whole strip is a drag region except for the interactive
      // controls (which opt out via the `.no-drag` class). Double-clicking
      // the empty area also toggles maximize, matching the default Windows
      // title bar behavior.
      onDoubleClick={handleToggleMaximize}
      className={cn(
        'drag-region flex w-full shrink-0 items-center bg-[var(--bg-subtle)]',
        'select-none',
        'h-8',
      )}
    >
      {/* Sidebar toggle. Lives inside the drag region so dragging the
          area around the button still moves the window. */}
      <button
        type="button"
        onClick={onToggleSidebar}
        onDoubleClick={(e) => e.stopPropagation()}
        aria-label="Toggle sidebar"
        title="Toggle sidebar"
        className={cn(
          'no-drag press-scale flex h-full w-9 items-center justify-center',
          'text-[var(--text-muted)] transition-colors duration-75',
          'hover:text-[var(--text-primary)]',
          'focus:outline-none',
        )}
      >
        <HugeiconsIcon icon={PanelLeftIcon} className="h-4 w-4" />
      </button>

      {/* Spacer pushes the window-control buttons to the right. */}
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
