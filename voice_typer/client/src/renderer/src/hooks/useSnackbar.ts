// src/renderer/src/hooks/useSnackbar.ts
//
// UX-013: extracted from the 5 duplicated snackbar implementations in
// Settings.tsx, Models.tsx, Vocabulary.tsx, Templates.tsx, Microphone.tsx.
// Each page had its own useState + setTimeout + JSX rendering.  This
// hook consolidates the pattern into a single reusable function.
//
// Usage:
//   const { snackbar, showSnack } = useSnackbar()
//   showSnack('Saved!', 'success')
//   // In JSX:
//   {snackbar && <Snackbar ... />}

import { useState, useCallback, useRef } from 'react'

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

  return { snackbar, showSnack, clearSnack }
}
