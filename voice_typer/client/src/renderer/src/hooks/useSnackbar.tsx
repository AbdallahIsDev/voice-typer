// src/renderer/src/hooks/useSnackbar.tsx
//
// UX-013: extracted from the 5 duplicated snackbar implementations in
// Settings.tsx, Models.tsx, Vocabulary.tsx, Templates.tsx, Microphone.tsx.
// Each page had its own useState + setTimeout + JSX rendering.  This
// hook consolidates the pattern into a single reusable function that
// also provides the Snackbar renderer component.
//
// Usage:
//   const { showSnack, Snackbar } = useSnackbar()
//   showSnack('Saved!', 'success')
//   // In JSX:
//   <Snackbar />
//
// IMPORTANT: This file MUST be .tsx (not .ts) because it contains JSX
// for the Snackbar component.  Vite resolves .ts before .tsx in
// extension priority, so a coexisting .ts file would shadow this one.

import { useState, useCallback, useRef } from 'react'
import { cn } from '../lib/utils'

export type SnackbarType = 'success' | 'error' | 'warning'

export interface SnackbarState {
  message: string
  type: SnackbarType
}

export function useSnackbar(timeoutMs = 3000) {
  const [snackbar, setSnackbar] = useState<SnackbarState | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showSnack = useCallback(
    (message: string, type: SnackbarType = 'success') => {
      setSnackbar({ message, type })
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
      timerRef.current = setTimeout(() => setSnackbar(null), timeoutMs)
    },
    [timeoutMs],
  )

  const clearSnack = useCallback(() => {
    setSnackbar(null)
    if (timerRef.current) {
      clearTimeout(timerRef.current)
    }
  }, [])

  const Snackbar = useCallback(() => {
    if (!snackbar) return null
    return (
      <div
        className={cn(
          'animate-slide-up fixed bottom-6 left-1/2 z-50 -translate-x-1/2',
          'rounded-lg px-4 py-2.5 text-sm shadow-lg',
          snackbar.type === 'success' && 'bg-primary text-primary-foreground',
          snackbar.type === 'error' && 'bg-destructive text-white',
          snackbar.type === 'warning' && 'bg-primary text-primary-foreground',
        )}
      >
        {snackbar.message}
      </div>
    )
  }, [snackbar])

  return { snackbar, showSnack, clearSnack, Snackbar }
}
