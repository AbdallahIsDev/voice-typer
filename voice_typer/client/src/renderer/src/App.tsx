import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { Sidebar } from '@/components/Sidebar'
import { TitleBar } from '@/components/TitleBar'
import { Toaster } from '@/components/ui/sonner'
import { Button } from '@/components/ui/button'
import Home from '@/pages/Home'
import HistoryPage from '@/pages/History'
import TemplatesPage from '@/pages/Templates'
import VocabularyPage from '@/pages/Vocabulary'
import ModelsPage from '@/pages/Models'
import MicrophonePage from '@/pages/Microphone'
import DashboardPage from '@/pages/Dashboard'
import SettingsPage from '@/pages/Settings'
// NEW-UX-009: About/Diagnostics page.
import AboutPage from '@/pages/About'
// #8: Onboarding wizard — was previously dead code (275-line component
// never imported, never rendered). Now wired in via the first-run check
// in the connection lifecycle effect below.
import OnboardingPage from '@/pages/Onboarding'
import { cn } from '@/lib/utils'
import type { RecordingState, Page, WindowBridge } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'
import { Spinner } from '@/components/Spinner'
// NEW-UX-015: ErrorBoundary catches render errors so a single bad
// config or component crash doesn't white-screen the entire app.
import { ErrorBoundary } from '@/components/ErrorBoundary'

// NEW-TS-012: runtime validator for the RecordingState string-literal
// union.  The backend emits status values as plain strings over IPC;
// previously we cast them to ``RecordingState`` without validation,
// which would silently propagate unknown values through the type
// system.  This validator returns ``null`` for unknown values so the
// caller can discard them instead of corrupting React state.
const RECORDING_STATES: ReadonlySet<string> = new Set([
  'idle',
  'recording',
  'transcribing',
  'loading',
  'cancelling',
  'error',
])

function asRecordingState(value: unknown): RecordingState | null {
  if (typeof value !== 'string') return null
  return RECORDING_STATES.has(value) ? (value as RecordingState) : null
}

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

  const { call } = usePython()
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
  // NEW-TS-015: removed the ``if (!isReady) return`` guard — it was
  // dead code (``isReady`` was always ``true`` because the preload
  // installs ``window.python`` before React mounts).  The actual
  // backend-readiness check is ``connectionStatus === 'connected'``,
  // which is set by the second useEffect below.
  useEffect(() => {
    const loadTheme = async () => {
      try {
        const cfg = await call<VoiceTyperConfig>('get_config')
        if (cfg?.theme_mode) setThemeMode(cfg.theme_mode)
      } catch {}
    }
    loadTheme()
  }, [call])

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
    // NEW-TS-015: removed the ``if (!isReady) return`` guard — it was
    // dead code (``isReady`` was always ``true``).

    let retries = 0
    const maxRetries = 5
    let timer: ReturnType<typeof setTimeout>
    let cancelled = false

    const checkConnection = async () => {
      if (cancelled) return
      try {
        const cfg = await call<VoiceTyperConfig>('get_config')
        if (!cancelled) {
          setConnectionStatus('connected')
          // Sync current state from backend (status_change events sent before
          // the React app mounted are lost — this ensures we catch up)
          call<{ status: string }>('get_status').then((s) => {
            // NEW-TS-012: removed the ``as RecordingState`` cast.
            // Casting an unvalidated string to a string-literal union
            // type hides bugs — if the backend ever emits a value
            // outside the union, the cast silently produces an invalid
            // RecordingState that the rest of the type system trusts.
            // We now validate at runtime and discard unknown values.
            if (!cancelled && s?.status) {
              const validated = asRecordingState(s.status)
              if (validated) setRecordingState(validated)
            }
          }).catch(() => {})
          // Send saved bubble_position to the Electron main process
          // so it persists across restarts (main process initializes to 'top')
          const pos = cfg?.bubble_position
          if (pos === 'bottom' || pos === 'top') {
            window.bubble?.setPosition?.(pos)
          }
          // Sync saved bubble_draggable state so the main process has the
          // correct value before the bubble is ever shown
          const draggable = cfg?.bubble_draggable
          if (typeof draggable === 'boolean') {
            window.bubble?.setDraggable?.(draggable)
          }
          // Show the bubble at startup if always_visible + show_on_startup is enabled.
          // This is a reliable fallback in case the TCP push event from Python's
          // _do_startup arrives before Electron is fully ready to render the bubble.
          const behavior = cfg?.bubble_behavior
          const showOnStartup = cfg?.bubble_show_on_startup
          if (behavior === 'always_visible' && showOnStartup !== false) {
            window.bubble?.show?.()
          }

          // #8: Onboarding wizard — detect first run and route the user
          // to the wizard. Previously this 275-line component was dead
          // code. The backend's `onboarding_is_first_run` IPC route
          // checks config.onboarding_completed (and the marker file).
          // We only auto-route on the very first successful connection
          // (when currentPage is still the default 'home'); once the
          // user navigates away we don't force them back.
          if (currentPage === 'home' && !cancelled) {
            try {
              const fr = await call<{ is_first_run: boolean }>('onboarding_is_first_run')
              if (!cancelled && fr?.is_first_run) {
                navigate('onboarding')
              }
            } catch {
              // Older backend without the IPC route — silently ignore.
            }
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
  }, [call, currentPage, navigate])

  // UX-031: Ctrl+B toggles the sidebar — discoverable keyboard shortcut
  // matching VS Code / Chrome's convention. Without this the collapse
  // button is invisible at width 0px in the collapsed state.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'b' && !e.shiftKey && !e.altKey) {
        // Don't intercept when typing in an input/textarea
        const target = e.target as HTMLElement | null
        const tag = target?.tagName?.toLowerCase() ?? ''
        if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return
        e.preventDefault()
        setSidebarCollapsed((c) => !c)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

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
    // NEW-TS-012: validate at runtime instead of casting to RecordingState.
    if (data?.status) {
      const validated = asRecordingState(data.status)
      if (validated) {
        setRecordingState(validated)
        setLastError(null)
      }
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

  // #8: Called by the Onboarding wizard after the user finishes (apply
  // or skip). Routes the user back to home and reloads the config so
  // the rest of the UI sees the user's onboarding choices.
  const handleOnboardingComplete = useCallback(async () => {
    navigate('home')
    // Reload the config so theme/hotkey/mic/model selections take effect
    try {
      const cfg = await call<VoiceTyperConfig>('get_config')
      if (cfg?.theme_mode) setThemeMode(cfg.theme_mode)
    } catch {
      // non-fatal — config will be re-read on next mount
    }
  }, [navigate, call])

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
        return <SettingsPage onThemeChange={handleThemeChange} onNavigate={navigate} />
      case 'about':
        // NEW-UX-009: About/Diagnostics page.
        return <AboutPage />
      case 'onboarding':
        return <OnboardingPage onComplete={handleOnboardingComplete} />
    }
  }

  // ── Render ────────────────────────────────────────────────────

  // NEW-UX-015: wrap the entire app in ErrorBoundary so a render error
  // in any page/component shows a recovery UI instead of white-screening.
  return (
    <ErrorBoundary>
    {/* NEW-A11Y-004: Skip-to-main-content link for keyboard users.
        Visually hidden until focused, then appears as a floating button.
        WCAG 2.1 SC 2.4.1 (Bypass Blocks). */}
    <a
      href="#main-content"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-100 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
    >
      Skip to main content
    </a>
    <div className={cn('flex h-screen flex-col bg-(--bg-subtle) font-sans text-(--text-primary) overflow-hidden', !isMaximized && 'rounded-lg border border-border')}>
      <TitleBar
        onToggleSidebar={() => setSidebarCollapsed((c) => !c)}
        onGoBack={goBack}
        onGoForward={goForward}
        canGoBack={navIndex.current > 0}
        canGoForward={navIndex.current < navHistory.current.length - 1}
        // NEW-TS-007: pass isMaximized down so TitleBar doesn't need
        // its own subscription to bridge.onMaximizedChanged.
        isMaximized={isMaximized}
      />

      <div className="flex min-h-0 flex-1">
        <Sidebar
          currentPage={currentPage}
          onNavigate={navigate}
          themeMode={themeMode}
          onThemeChange={handleThemeChange}
          collapsed={sidebarCollapsed}
        />

          <div className="flex min-w-0 flex-1 flex-col">
          <main id="main-content" role="main" className="flex-1 overflow-y-auto rounded-l-xl border-border border border-r-0 border-b-0 bg-(--bg)" style={{ scrollbarGutter: 'stable' }}>
            {connectionStatus === 'connecting' ? (
              <div className="flex h-full flex-col items-center justify-center gap-3">
                <Spinner />
                <p className="text-sm text-(--text-muted)">
                  Starting Python backend...
                </p>
              </div>
            ) : connectionStatus === 'disconnected' ? (
              <div className="flex h-full flex-col items-center justify-center gap-4">
                <p className="text-sm text-destructive">
                  Lost connection to Python backend
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleRetryConnection}
                >
                  Retry Connection
                </Button>
              </div>
            ) : (
              renderPage()
            )}
          </main>
        </div>
      </div>
      <Toaster />

      {/* #9: Screen reader live region for dynamic status updates.
          NEW-A11Y-002: NVDA/JAWS/VoiceOver users press F2, hear nothing,
          don't know if recording started.  This aria-live region announces
          state transitions so screen reader users know what's happening. */}
      <div aria-live="polite" className="sr-only">
        {recordingState === 'recording' ? 'Recording started.' : ''}
        {recordingState === 'transcribing' ? 'Transcribing audio…' : ''}
        {recordingState === 'idle' ? 'Ready.' : ''}
        {recordingState === 'error' ? 'Error occurred.' : ''}
        {recordingState === 'loading' ? 'Loading model…' : ''}
        {recordingState === 'cancelling' ? 'Cancelling…' : ''}
      </div>
    </div>
    </ErrorBoundary>
  )
}

