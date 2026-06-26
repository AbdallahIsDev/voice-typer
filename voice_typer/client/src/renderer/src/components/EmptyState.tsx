import type { ReactNode } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import type { IconSvgElement } from '@hugeicons/react'
import { Add01Icon } from '@hugeicons/core-free-icons'
import { Button } from '@/components/ui/button'

interface EmptyStateProps {
  icon: IconSvgElement
  title: string
  description?: string
  /** Optional action button label */
  actionLabel?: string
  /** Optional action button click handler */
  onAction?: () => void
  /** Optional — overrides the default Add01Icon for the action button */
  actionIcon?: IconSvgElement
  /** Optional extra content below the description */
  children?: ReactNode
}

export function EmptyState({
  icon: _icon,
  title,
  description,
  actionLabel,
  onAction,
  actionIcon,
  children,
}: EmptyStateProps) {
  const displayIcon = actionIcon ?? Add01Icon
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16">
      <HugeiconsIcon
        icon={_icon}
        strokeWidth={1.625}
        className="h-10 w-10 text-(--text-muted) opacity-30"
      />
      <p className="text-sm text-(--text-muted)">{title}</p>
      {description && (
        <p className="text-xs text-(--text-muted) opacity-70">{description}</p>
      )}
      {children}
      {actionLabel && onAction && (
        <Button variant="default" className="mt-2 gap-2" onClick={onAction}>
          <HugeiconsIcon icon={displayIcon} strokeWidth={1.625} className="h-4 w-4" />
          {actionLabel}
        </Button>
      )}
    </div>
  )
}
