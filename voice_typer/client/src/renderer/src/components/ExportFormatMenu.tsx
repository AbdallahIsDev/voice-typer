import { useState, useEffect, useRef } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { Download01Icon } from '@hugeicons/core-free-icons'
import { Button } from '@/components/ui/button'

interface ExportFormatMenuProps {
  onExport: (format: 'json' | 'csv') => void | Promise<void>
  disabled?: boolean
}

export default function ExportFormatMenu({ onExport, disabled }: ExportFormatMenuProps) {
  const [show, setShow] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  // Close on click outside
  useEffect(() => {
    if (!show) return
    const close = (e: MouseEvent) => {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        btnRef.current &&
        !btnRef.current.contains(e.target as Node)
      ) {
        setShow(false)
      }
    }
    // Use a microtask delay so the current click that opened the menu
    // doesn't immediately close it
    const id = setTimeout(() => document.addEventListener('click', close), 0)
    return () => {
      clearTimeout(id)
      document.removeEventListener('click', close)
    }
  }, [show])

  return (
    <div className="relative">
      <Button
        ref={btnRef}
        variant="outline"
        size="sm"
        onClick={() => setShow(prev => !prev)}
        disabled={disabled}
        className="gap-2"
        aria-haspopup="menu"
        aria-expanded={show}
      >
        <HugeiconsIcon icon={Download01Icon} strokeWidth={1.625} className="h-4 w-4" />
        Export
      </Button>
      {show && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Export format"
          className="absolute right-0 top-full mt-1 z-10 w-30 rounded-xl border border-border bg-(--bg-subtle) shadow-lg overflow-hidden"
        >
          <button
            role="menuitem"
            onClick={() => {
              setShow(false)
              onExport('json')
            }}
            className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
          >
            Export as JSON
          </button>
          <button
            role="menuitem"
            onClick={() => {
              setShow(false)
              onExport('csv')
            }}
            className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
          >
            Export as CSV
          </button>
        </div>
      )}
    </div>
  )
}
