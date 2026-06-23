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
      const result = (await api.call({ type, data })) as Record<string, unknown>
      if (result && typeof result === 'object' && '_error' in result) {
        throw new Error(result._error as string)
      }
      return result as T
    },
    [api],
  )

  // NEW-TS-015: previously this hook also returned ``isReady: !!api``.
  // That flag was always ``true`` in production because the preload
  // script installs ``window.python`` before the React app mounts, so
  // every consumer's ``if (!isReady) return`` guard was dead code.
  // Worse, the name suggested "Python backend is ready" when it
  // actually meant "Python bridge exists" — callers that wanted real
  // readiness should track ``connectionStatus === 'connected'`` in
  // App.tsx (which probes the backend via ``get_config``).
  //
  // If a future caller needs to distinguish "bridge installed" from
  // "bridge missing" (e.g. running outside Electron), they can do
  // ``const api = (window as unknown as WindowWithPython).python`` and
  // check ``!!api`` directly.  We don't expose a misleading flag.
  return { call }
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
