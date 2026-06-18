import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { Sidebar } from '@/components/Sidebar'
import { TitleBar } from '@/components/TitleBar'
import { Toaster } from '@/components/ui/sonner'
import Home from '@/pages/Home'
import HistoryPage from '@/pages/History'
import TemplatesPage from '@/pages/Templates'
import VocabularyPage from '@/pages/Vocabulary'
import ModelsPage from '@/pages/Models'
import MicrophonePage from '@/pages/Microphone'
import DashboardPage from '@/pages/Dashboard'
import SettingsPage from '@/pages/Settings'
import { cn } from '@/lib/utils'
import type { RecordingState, Page, WindowBridge } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'

export default function App() {
  // ── Routing ───────────────────────────────────────────────────

  const [currentPage, setCurrentPage] = useState<Page>('home')
  const navHistory = useRef<Page[]>(['home'])
  const navIndex = useRef(0)

  const navigate = useCallback((page: Page) => {
    navHistory.current = [...navHistory.current.slice(0, navIndex.current + 1), page]
    navIndex.current++
    setCurrentPage(page)
  }, [])

  const goBack = useCallback(() => {
    if (navIndex.current > 0) {
      navIndex.current--
      setCurrentPage(navHistory.current[navIndex.current])
    }
  }, [])

  const goForward = useCallback(() => {
    if (navIndex.current < navHistory.current.length - 1) {
      navIndex.current++
      setCurrentPage(navHistory.current[navIndex.current])
    }
  }, [])

  // Mouse forward/back buttons (X1/X2) navigate like a browser
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (e.button === 3) {
        e.preventDefault()
        goBack()
      } else if (e.button === 4) {
        e.preventDefault()
        goForward()
      }
    }
    document.addEventListener('mouseup', handler)
    return () => document.removeEventListener('mouseup', handler)
  }, [goBack, goForward])

  // ── Global state (pushed down from app level) ─────────────────

  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [connectionStatus, setConnectionStatus] = useState<
    'connected' | 'disconnected' | 'connecting'
  >('connecting')
  const [lastError, setLastError] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [isMaximized, setIsMaximized] = useState(false)

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

  // ── Window maximize state (for removing border-radius when maximized) ──

  const bridge = typeof window !== 'undefined' ? window.window_ as WindowBridge : undefined

  useEffect(() => {
    if (!bridge) return
    let cancelled = false
    bridge.isMaximized().then((v) => { if (!cancelled) setIsMaximized(v) }).catch(() => {})
    const unsub = bridge.onMaximizedChanged((v) => { if (!cancelled) setIsMaximized(v) })
    return () => { cancelled = true; unsub() }
  }, [bridge])

  // ── Connection lifecycle ──────────────────────────────────────

  useEffect(() => {
    if (!isReady) return

    let retries = 0
    const maxRetries = 5
    let timer: ReturnType<typeof setTimeout>
    let cancelled = false

    const checkConnection = async () => {
      if (cancelled) return
      try {
        const cfg = await call<any>('get_config')
        if (!cancelled) {
          setConnectionStatus('connected')
          // Sync current state from backend (status_change events sent before
          // the React app mounted are lost — this ensures we catch up)
          call<{ status: string }>('get_status').then((s) => {
            if (!cancelled && s?.status) {
              setRecordingState(s.status as RecordingState)
            }
          }).catch(() => {})
          // Send saved bubble_position to the Electron main process
          // so it persists across restarts (main process initializes to 'top')
          const pos = (cfg as any)?.bubble_position as string | undefined
          if (pos === 'bottom' || pos === 'top') {
            ;(window as any).bubble?.setPosition?.(pos)
          }
          // Sync saved bubble_draggable state so the main process has the
          // correct value before the bubble is ever shown
          const draggable = (cfg as any)?.bubble_draggable
          if (typeof draggable === 'boolean') {
            ;(window as any).bubble?.setDraggable?.(draggable)
          }
          // Show the bubble at startup if always_visible + show_on_startup is enabled.
          // This is a reliable fallback in case the TCP push event from Python's
          // _do_startup arrives before Electron is fully ready to render the bubble.
          const behavior = (cfg as any)?.bubble_behavior as string | undefined
          const showOnStartup = (cfg as any)?.bubble_show_on_startup
          if (behavior === 'always_visible' && showOnStartup !== false) {
            ;(window as any).bubble?.show?.()
          }
        }
      } catch {
        retries++
        if (!cancelled && retries < maxRetries) {
          timer = setTimeout(checkConnection, 2000)
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
    }, 60_000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [connectionStatus, call])

  // ── App-level event subscriptions ─────────────────────────────

  usePythonEvent('status_change', useCallback((data) => {
    if (data?.status) {
      setRecordingState(data.status as RecordingState)
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
      await call('set_config', { theme_mode: mode })
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
        return <Home recordingState={recordingState} lastError={lastError} onNavigate={navigate} />
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
      case 'analytics':
        return <DashboardPage onNavigate={navigate} />
      case 'settings':
        return <SettingsPage onThemeChange={handleThemeChange} />
    }
  }

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className={cn('flex h-screen flex-col bg-(--bg-subtle) font-sans text-(--text-primary) overflow-hidden', !isMaximized && 'rounded-lg border border-border')}>
      <TitleBar onToggleSidebar={() => setSidebarCollapsed((c) => !c)} />

      <div className="flex min-h-0 flex-1">
        <Sidebar
          currentPage={currentPage}
          onNavigate={navigate}
          themeMode={themeMode}
          onThemeChange={handleThemeChange}
          collapsed={sidebarCollapsed}
        />

          <div className="flex min-w-0 flex-1 flex-col">
          <main className="flex-1 overflow-y-auto rounded-l-xl border-border border border-r-0 border-b-0 bg-(--bg)" style={{ scrollbarGutter: 'stable' }}>
            {connectionStatus === 'connecting' ? (
              <div className="flex h-full flex-col items-center justify-center gap-3">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                <p className="text-sm text-(--text-muted)">
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
                    'rounded-lg bg-(--surface) px-4 py-2 text-sm',
                    'text-(--text-secondary) transition-colors',
                    'hover:bg-(--surface-hover) hover:text-text-primary',
                  )}
                >
                  Retry Connection
                </button>
              </div>
            ) : (
              renderPage()
            )}
          </main>
        </div>
      </div>
      <Toaster />
    </div>
  )
}

