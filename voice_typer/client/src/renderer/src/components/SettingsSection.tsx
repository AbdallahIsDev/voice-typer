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
        <h2 className="font-sans text-lg font-semibold text-(--text-primary)">{title}</h2>
        {description && (
          <p className="text-sm text-(--text-muted)">{description}</p>
        )}
      </div>
      <div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
        {children}
      </div>
    </section>
  )
}
