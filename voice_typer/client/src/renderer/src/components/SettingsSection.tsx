// src/renderer/src/components/SettingsSection.tsx

import type { ReactNode } from 'react'

interface SettingsSectionProps {
  title: string
  description?: string
  children: ReactNode
}

export function SettingsSection({ title, description, children }: SettingsSectionProps) {
  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h2 className="font-serif text-lg font-semibold text-[var(--text-primary)]">{title}</h2>
        {description && (
          <p className="text-sm text-[var(--text-muted)]">{description}</p>
        )}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  )
}
