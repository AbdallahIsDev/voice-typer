import { useState, useEffect, useRef } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { Download01Icon } from '@hugeicons/core-free-icons'

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
      <button
        ref={btnRef}
        onClick={() => setShow(prev => !prev)}
        disabled={disabled}
        className="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium bg-(--bg-subtle) text-(--text-muted) hover:text-(--text-primary) transition-colors border border-border disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-(--text-muted)"
      >
        <HugeiconsIcon icon={Download01Icon} strokeWidth={1.625} className="h-4 w-4" />
        Export
      </button>
      {show && (
        <div
          ref={menuRef}
          className="absolute right-0 top-full mt-1 z-10 w-30 rounded-xl border border-border bg-(--bg-subtle) shadow-lg overflow-hidden"
        >
          <button
            onClick={() => {
              setShow(false)
              onExport('json')
            }}
            className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
          >
            Export as JSON
          </button>
          <button
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
