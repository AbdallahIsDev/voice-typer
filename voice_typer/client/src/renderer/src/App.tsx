import { useState, useEffect, useCallback } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { Sidebar } from '@/components/Sidebar'
import { StatusBar } from '@/components/StatusBar'
import Home from '@/pages/Home'
import HistoryPage from '@/pages/History'
import SettingsPage from '@/pages/Settings'
import { cn } from '@/lib/utils'
import type { RecordingState, Page } from '@/types/ipc'
import '@/styles/fonts.css'

export default function App() {
  // ── Routing ───────────────────────────────────────────────────

  const [currentPage, setCurrentPage] = useState<Page>('home')

  // ── Global state (pushed down from app level) ─────────────────

  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [connectionStatus, setConnectionStatus] = useState<
    'connected' | 'disconnected' | 'connecting'
  >('connecting')
  const [lastError, setLastError] = useState<string | null>(null)

  const { call, isReady } = usePython()

  // ── Connection lifecycle ──────────────────────────────────────

  useEffect(() => {
    if (!isReady) return

    let retries = 0
    const maxRetries = 10
    let timer: ReturnType<typeof setTimeout>
    let cancelled = false

    const checkConnection = async () => {
      if (cancelled) return
      try {
        await call('get_config')
        if (!cancelled) setConnectionStatus('connected')
      } catch {
        retries++
        if (!cancelled && retries < maxRetries) {
          timer = setTimeout(checkConnection, 1000)
        } else if (!cancelled) {
          setConnectionStatus('disconnected')
        }
      }
    }

    checkConnection()

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [isReady, call])

  // Periodic health check while connected
  useEffect(() => {
    if (connectionStatus !== 'connected') return

    let cancelled = false

    const interval = setInterval(async () => {
      try {
        await call('get_config')
      } catch {
        if (!cancelled) setConnectionStatus('disconnected')
      }
    }, 30_000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [connectionStatus, call])

  // ── App-level event subscriptions ─────────────────────────────

  usePythonEvent('status_change', useCallback((data) => {
    if (data?.status) {
      const state = data.status as string
      if (state === 'idle' || state === 'recording' || state === 'processing' || state === 'error') {
        setRecordingState(state as RecordingState)
      }
      setLastError(null)
    }
  }, []))

  usePythonEvent('error', useCallback((data) => {
    if (typeof data?.message === 'string') {
      setLastError(data.message)
    }
  }, []))

  // ── Reconnection handler (called by children on fatal errors) ─

  const handleRetryConnection = useCallback(async () => {
    setConnectionStatus('connecting')
    try {
      await call('get_config')
      setConnectionStatus('connected')
    } catch {
      setConnectionStatus('disconnected')
    }
  }, [call])

  // ── Page renderer ─────────────────────────────────────────────

  const renderPage = () => {
    switch (currentPage) {
      case 'home':
        return <Home recordingState={recordingState} lastError={lastError} />
      case 'settings':
        return <SettingsPage />
      case 'history':
        return <HistoryPage />
    }
  }

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className="flex h-screen bg-[var(--bg)] font-sans text-[var(--text-primary)]">
      <Sidebar currentPage={currentPage} onNavigate={setCurrentPage} />

      <div className="flex min-w-0 flex-1 flex-col">
        <main className="flex-1 overflow-hidden">
          {connectionStatus === 'connecting' ? (
            <div className="flex h-full flex-col items-center justify-center gap-3">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
              <p className="text-sm text-[var(--text-muted)]">
                Starting Python backend...
              </p>
            </div>
          ) : connectionStatus === 'disconnected' ? (
            <div className="flex h-full flex-col items-center justify-center gap-4">
              <p className="text-sm text-red-400">
                Lost connection to Python backend
              </p>
              <button
                onClick={handleRetryConnection}
                className={cn(
                  'rounded-lg bg-[var(--surface)] px-4 py-2 text-sm',
                  'text-[var(--text-secondary)] transition-colors',
                  'hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)]',
                )}
              >
                Retry Connection
              </button>
            </div>
          ) : (
            renderPage()
          )}
        </main>

        <StatusBar
          connectionStatus={connectionStatus}
          recordingState={recordingState}
        />
      </div>
    </div>
  )
}

