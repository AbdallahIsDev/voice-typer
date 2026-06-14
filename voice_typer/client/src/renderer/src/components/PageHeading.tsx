import type { ReactNode } from 'react'

interface PageHeadingProps {
  title: string
  description?: string
  children?: ReactNode
}

export default function PageHeading({ title, description, children }: PageHeadingProps) {
  return (
    <div className="space-y-1 pb-5">
      {children ? (
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <h1 className="font-sans text-2xl font-semibold tracking-tight text-(--text-primary)">
              {title}
            </h1>
            {description !== undefined ? (
              <p className="text-sm text-(--text-muted)">{description || '\u00A0'}</p>
            ) : null}
          </div>
          {children}
        </div>
      ) : (
        <>
          <h1 className="font-sans text-2xl font-semibold tracking-tight text-(--text-primary)">
            {title}
          </h1>
          {description !== undefined ? (
            <p className="text-sm text-(--text-muted)">{description || '\u00A0'}</p>
          ) : null}
        </>
      )}
    </div>
  )
}
