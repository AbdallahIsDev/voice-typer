// src/renderer/src/components/SettingRow.tsx

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface SettingRowProps {
  label: string
  description?: string
  children: ReactNode
  align?: 'start' | 'center'
}

export function SettingRow({ label, description, children, align = 'center' }: SettingRowProps) {
  return (
    <div
      className={cn(
        'flex items-start justify-between gap-6 px-3.5 py-2.5',
        align === 'center' && 'items-center',
      )}
    >
      <div className="space-y-0.5 min-w-0">
        <label className="text-sm font-medium text-(--text-primary) cursor-default">
          {label}
        </label>
        {description && (
          <p className="text-xs text-(--text-muted) leading-relaxed">{description}</p>
        )}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}
