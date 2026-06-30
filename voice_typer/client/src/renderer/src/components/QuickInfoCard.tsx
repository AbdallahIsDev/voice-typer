import type { ReactNode } from 'react'
import type { IconSvgElement } from '@hugeicons/react'
import { HugeiconsIcon } from '@hugeicons/react'

interface QuickInfoCardProps {
  icon: IconSvgElement
  label: string
  value: ReactNode
}

export function QuickInfoCard({ icon, label, value }: QuickInfoCardProps) {
  return (
    <div className="rounded-lg border border-border bg-(--bg-subtle) p-3.5 flex items-center gap-3">
      <div className="rounded-lg bg-accent/10 p-2">
        <HugeiconsIcon icon={icon} strokeWidth={2} className="h-4 w-4 text-accent" />
      </div>
      <div className="min-w-0">
        <p className="text-[11px] text-(--text-muted) font-medium">{label}</p>
        <p className="text-sm font-semibold text-(--text-primary) truncate">{value}</p>
      </div>
    </div>
  )
}
