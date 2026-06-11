// src/renderer/src/hooks/usePython.ts

import { useCallback, useEffect, useRef } from 'react'

type EventCallback = (event: { type: string; data?: Record<string, unknown> }) => void

interface WindowWithPython {
  python?: {
    call: (msg: { type: string; data?: Record<string, unknown> }) => Promise<unknown>
    onEvent: (callback: EventCallback) => () => void
  }
}

export function usePython() {
  const api = (window as unknown as WindowWithPython).python

  const call = useCallback(
    async <T = unknown>(type: string, data?: Record<string, unknown>): Promise<T> => {
      if (!api) throw new Error('Python bridge not available')
      const result = await api.call({ type, ...data })
      return result as T
    },
    [api],
  )

  return { call, isReady: !!api }
}

export function usePythonEvent(type: string, handler: (data?: Record<string, unknown>) => void) {
  const handlerRef = useRef(handler)
  handlerRef.current = handler

  useEffect(() => {
    const api = (window as unknown as WindowWithPython).python
    if (!api) return

    return api.onEvent((event) => {
      if (event.type === type) {
        handlerRef.current(event.data)
      }
    })
  }, [type])
}
