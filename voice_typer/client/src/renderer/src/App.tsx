import { useState, useEffect, useCallback } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { Sidebar } from '@/components/Sidebar'
import { StatusBar } from '@/components/StatusBar'
import { TitleBar } from '@/components/TitleBar'
import Home from '@/pages/Home'
import HistoryPage from '@/pages/History'
import TemplatesPage from '@/pages/Templates'
import VocabularyPage from '@/pages/Vocabulary'
import ModelsPage from '@/pages/Models'
import MicrophonePage from '@/pages/Microphone'
import PrivacyPage from '@/pages/Privacy'
import SettingsPage from '@/pages/Settings'
import { cn } from '@/lib/utils'
import type { RecordingState, Page } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'

export default function App() {
  // ── Routing ───────────────────────────────────────────────────

  const [currentPage, setCurrentPage] = useState<Page>('home')

  // ── Global state (pushed down from app level) ─────────────────

  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [connectionStatus, setConnectionStatus] = useState<
    'connected' | 'disconnected' | 'connecting'
  >('connecting')
  const [lastError, setLastError] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  const { call, isReady } = usePython()
  const [themeMode, setThemeMode] = useState<VoiceTyperConfig['theme_mode']>('system')

  // ── Theme detection & application ────────────────────────────

  useEffect(() => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)')

    const applyTheme = (mode: string) => {
      let isDark: boolean
      if (mode === 'dark') {
        isDark = true
      } else if (mode === 'light') {
        isDark = false
      } else {
        isDark = prefersDark.matches
      }
      document.documentElement.classList.toggle('dark', isDark)
    }

    // Apply current theme
    applyTheme(themeMode)

    // Listen for system changes when in 'system' mode
    const handler = () => {
      if (themeMode === 'system') {
        applyTheme('system')
      }
    }
    prefersDark.addEventListener('change', handler)
    return () => prefersDark.removeEventListener('change', handler)
  }, [themeMode])

  // Load theme from config on mount
  useEffect(() => {
    if (!isReady) return
    const loadTheme = async () => {
      try {
        const cfg = await call<any>('get_config')
        if (cfg?.theme_mode) setThemeMode(cfg.theme_mode)
      } catch {}
    }
    loadTheme()
  }, [isReady, call])

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
  }, []))  // ── Theme change handler (save to config) ─────────────────────

  const handleThemeChange = useCallback(async (mode: VoiceTyperConfig['theme_mode']): Promise<void> => {
    setThemeMode(mode)
    try {
      await call('set_config', { data: { theme_mode: mode } })
    } catch {
      // Theme is local-only if backend unavailable
    }
  }, [call])

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
      case 'history':
        return <HistoryPage />
      case 'templates':
        return <TemplatesPage />
      case 'vocabulary':
        return <VocabularyPage />
      case 'models':
        return <ModelsPage />
      case 'microphone':
        return <MicrophonePage />
      case 'privacy':
        return <PrivacyPage />
      case 'settings':
        return <SettingsPage />
    }
  }

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className="flex h-screen flex-col bg-[var(--bg)] font-sans text-[var(--text-primary)]">
      <TitleBar onToggleSidebar={() => setSidebarCollapsed((c) => !c)} />

      <div className="flex min-h-0 flex-1">
        <Sidebar
          currentPage={currentPage}
          onNavigate={setCurrentPage}
          themeMode={themeMode}
          onThemeChange={handleThemeChange}
          collapsed={sidebarCollapsed}
        />

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
                <p className="text-sm text-destructive">
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
    </div>
  )
}

