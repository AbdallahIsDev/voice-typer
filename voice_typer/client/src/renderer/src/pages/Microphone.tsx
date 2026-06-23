import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython } from '@/hooks/usePython'
import { useSnackbar } from '@/hooks/useSnackbar'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  Mic02Icon,
  MicOff01Icon,
  PlayIcon,
  StopIcon,
} from '@hugeicons/core-free-icons'
import PageHeading from '@/components/PageHeading'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { VoiceTyperConfig } from '@/types/config'
import { Spinner } from '@/components/Spinner'

// Module-level cache — persists across page navigations so microphone settings
// render instantly on re-visit instead of showing a loading spinner.
let _cachedMicrophones: MicDevice[] = []
let _cachedConfig: VoiceTyperConfig | null = null

interface MicDevice {
  index: number
  id?: string
  name: string
  host_api: string
  default?: boolean
  channels?: number
  rate?: number
}

export default function MicrophonePage() {
  const { call } = usePython()
  const [microphones, setMicrophones] = useState<MicDevice[]>(_cachedMicrophones)
  const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig)
  const [loading, setLoading] = useState(true)
  const [testRunning, setTestRunning] = useState(false)
  const [level, setLevel] = useState(0)
  // UX-013: use extracted useSnackbar hook instead of inline implementation
  const { snackbar, showSnack } = useSnackbar()
  const levelIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [mics, cfg] = await Promise.all([
        call<MicDevice[]>('get_microphones'),
        call<VoiceTyperConfig>('get_config'),
      ])
      _cachedMicrophones = Array.isArray(mics) ? mics : []
      _cachedConfig = cfg
      setMicrophones(_cachedMicrophones)
      setConfig(cfg)
    } catch (err) {
      console.error('Failed to load microphone data:', err)
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => { loadData() }, [loadData])

  // Cleanup test interval on unmount
  useEffect(() => {
    return () => {
      if (levelIntervalRef.current) {
        clearInterval(levelIntervalRef.current)
      }
    }
  }, [])

  const activeMicId = config?.microphone ?? null
  const isSystemDefault = activeMicId === null

  const selectMicrophone = async (micId: string | null) => {
    try {
      await call('set_config', { microphone: micId })
      setConfig((prev) => (prev ? { ...prev, microphone: micId } : prev))
      const label = micId === null ? 'System Default' : microphones.find((m) => (m.id ?? String(m.index)) === micId)?.name ?? 'Microphone'
      showSnack(`Using: ${label}`, 'success')
    } catch {
      showSnack('Failed to set microphone', 'error')
    }
  }

  const startTest = () => {
    // DEAD-021-025: previously this generated fake random audio
    // levels.  We now show an honest "not implemented" message
    // instead of misleading the user with simulated data.  The real
    // mic test would require a new IPC route that streams audio
    // levels from the Python backend.
    showSnack(
      'Microphone test is not yet implemented. Try recording a short dictation to verify your mic works.',
      'warning',
    )
  }

  const stopTest = () => {
    setTestRunning(false)
    if (levelIntervalRef.current) {
      clearInterval(levelIntervalRef.current)
      levelIntervalRef.current = null
    }
    setLevel(0)
    showSnack('Microphone test stopped', 'warning')
  }

  const getLevelColor = (lvl: number) => {
    if (lvl > 0.7) return 'var(--destructive)'
    if (lvl > 0.3) return 'var(--primary)'
    return 'var(--accent)'
  }

  // Only show the full-page spinner on cold start (no cache).
  // On revisit with cached data, the content renders immediately
  // while loadData() refreshes silently in the background.
  if (!_cachedMicrophones.length && !_cachedConfig && loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
      <PageHeading
        title="Microphone"
        description="Select and test your microphone"
      />

      <div className="space-y-6">
        {/* System Default Card */}
        <div
          className={cn(
            'rounded-xl border p-5 transition-colors',
            isSystemDefault
              ? 'border-accent bg-(--bg-subtle)'
              : 'border-border bg-(--bg-subtle)',
          )}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <HugeiconsIcon
                icon={Mic02Icon}
                strokeWidth={1.625}
                className={cn(
                  'h-4 w-4',
                  isSystemDefault ? 'text-primary' : 'text-(--text-muted)',
                )}
              />
              <div>
                <p className="text-sm font-semibold text-(--text-primary)">
                  System Default
                </p>
                <p className="text-xs text-(--text-muted)">
                  Use the operating system's default input device
                </p>
              </div>
            </div>
            {isSystemDefault ? (
              <span className="inline-flex items-center rounded-md px-2.5 py-0.5 text-[10px] font-semibold border border-primary/20 bg-primary/10 text-primary">
                Active
              </span>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => selectMicrophone(null)}
              >
                Use
              </Button>
            )}
          </div>

          {/* Level bar when active */}
          {isSystemDefault && (
            <div
              className="mt-3 h-1.5 w-full rounded-full bg-border overflow-hidden"
              role="progressbar"
              aria-label="Microphone input level"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(level * 100)}
            >
              <div
                className="h-full rounded-full transition-all duration-150"
                style={{
                  width: `${level * 100}%`,
                  backgroundColor: getLevelColor(level),
                }}
              />
            </div>
          )}
        </div>

        {/* Microphone Test Area */}
        <div className="flex items-center gap-3">
          <Button
            variant="default"
            size="sm"
            className="gap-2"
            onClick={startTest}
            disabled={testRunning}
          >              <HugeiconsIcon icon={PlayIcon} strokeWidth={1.625} className="h-4 w-4" />
            Start Test
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={stopTest}
            disabled={!testRunning}
          >
            <HugeiconsIcon icon={StopIcon} strokeWidth={1.625} className="h-4 w-4" />
            Stop Test
          </Button>
          <span className="text-xs text-(--text-muted) ml-auto">
            Level: {Math.round(level * 100)}%
          </span>
        </div>

        {/* Available Microphones List */}
        {microphones.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <HugeiconsIcon icon={MicOff01Icon} strokeWidth={1.625} className="h-10 w-10 text-(--text-muted) opacity-30" />
            <p className="text-sm text-(--text-muted)">No microphones found</p>
            <p className="text-xs text-(--text-muted) opacity-70">
              Connect a microphone and restart
            </p>
          </div>
        ) : (
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-(--text-muted) mb-2 px-1">
              Available Microphones
            </p>
            <div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
              {microphones.map((mic) => {
                const micId = mic.id ?? String(mic.index)
                const isActive = micId === activeMicId
                return (
                  <div key={micId} className="flex items-center gap-3 px-3.5 py-2.5">
                    <HugeiconsIcon
                      icon={Mic02Icon}
                      strokeWidth={1.625}
                      className={cn('h-4 w-4 shrink-0', isActive ? 'text-primary' : 'text-(--text-muted)')}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-medium text-(--text-primary) truncate">
                          {mic.name}
                        </p>
                        {mic.default && (
                          <span className="shrink-0 inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-[9px] font-semibold text-white">
                            Default
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-(--text-muted) mt-0.5">
                        Channels: {mic.channels ?? 1} &middot; Rate: {mic.rate ?? 44100}Hz
                      </p>
                    </div>
                    {isActive ? (
                      <span className="shrink-0 inline-flex items-center rounded-md px-2.5 py-0.5 text-[10px] font-semibold border border-primary/20 bg-primary/10 text-primary">
                        Active
                      </span>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        className="shrink-0 cursor-pointer"
                        onClick={() => selectMicrophone(micId)}
                      >
                        Use
                      </Button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Snackbar */}
      {snackbar && (
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
      )}
    </div>
  )
}
