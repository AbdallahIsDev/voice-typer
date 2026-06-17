import { useState, useEffect, useCallback, useRef } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { Mic02Icon, StopIcon } from '@hugeicons/core-free-icons'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { cn } from '@/lib/utils'
import StatCards from '@/components/StatCards'
import ActivityList from '@/components/ActivityList'
import type { RecordingState, TodayStats, HistoryRecord, Page } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'

// Module-level cache — persists across page navigations so the recent activity
// section renders instantly on re-visit instead of appearing from nowhere.
let _cachedRecent: HistoryRecord[] = []

interface HomeProps {
  recordingState: RecordingState
  lastError: string | null
  onNavigate?: (page: Page) => void
}

const STATUS_COLORS: Record<string, string> = {
  idle: '#22C55E',
  recording: '#FF3333',
  processing: '#2563EB',
  transcribing: '#7C3AED',
  loading: '#F59E0B',
  warming_up: '#E67E22',
  downloading: '#34495E',
  paused: '#9B59B6',
  cancelling: '#C0392B',
  setup: '#2980B9',
  not_configured: '#95A5A6',
  error: '#FF3333',
}

const STATUS_LABELS: Record<string, string> = {
  idle: 'READY',
  recording: 'RECORDING',
  processing: 'PROCESSING',
  transcribing: 'TRANSCRIBING',
  loading: 'LOADING',
  warming_up: 'WARMING UP',
  downloading: 'DOWNLOADING',
  paused: 'PAUSED',
  cancelling: 'CANCELLING',
  setup: 'SETTING UP',
  not_configured: 'NOT CONFIGURED',
  error: 'ERROR',
}

function statusKeyFor(state: RecordingState, hasError: boolean): string {
  // Normalize listening → idle
  if (state === 'listening') return 'idle'
  // When there's an error and the state is error, keep it as error
  if (state === 'error' && hasError) return 'error'
  return state
}

export default function Home({ recordingState, lastError, onNavigate }: HomeProps) {
  const { call } = usePython()

  const [hotkey, setHotkey] = useState('F2')
  const [lastText, setLastText] = useState('')
  const [stats, setStats] = useState<TodayStats | null>(null)
  const [recent, setRecent] = useState<HistoryRecord[]>(_cachedRecent)
  const [toggling, setToggling] = useState(false)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const cfg = await call<VoiceTyperConfig>('get_config')
        if (cancelled) return
        const raw = cfg?.hotkey ?? '<F2>'
        setHotkey(raw.replace(/[<>]/g, ''))
      } catch {}
      try {
        const s = await call<TodayStats>('get_today_stats')
        if (cancelled) return
        setStats(s)
      } catch {}
      try {
        const h = await call<HistoryRecord[]>('get_history', { limit: 4 })
        if (cancelled) return
        _cachedRecent = h ?? []
        setRecent(_cachedRecent)
      } catch {}
    }
    load()
    return () => { cancelled = true }
  }, [call])

  usePythonEvent('transcription_final', (data) => {
    if (typeof data?.text === 'string' && data.text.trim()) {
      setLastText(data.text)
    }
  })

  usePythonEvent('recording_started', () => {
    setLastText('')
  })

  // ── Proactive background refresh after new transcriptions ────────
  //
  // When a transcription_final event arrives, silently refresh the cached
  // recent records and today's stats so the Home page shows accurate data
  // on next visit (or immediately if already on Home).
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  usePythonEvent('transcription_final', useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(async () => {
      try {
        const [newRecent, newStats] = await Promise.all([
          call<HistoryRecord[]>('get_history', { limit: 5 }),
          call<TodayStats>('get_today_stats'),
        ])
        if (newRecent) {
          _cachedRecent = newRecent
          setRecent(newRecent)
        }
        if (newStats) setStats(newStats)
      } catch {
        // Silently ignore — next manual load picks up fresh data
      }
    }, 500)
  }, [call]))

  // Clean up pending refresh timer on unmount
  useEffect(() => {
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
    }
  }, [])

  const handleToggle = useCallback(async () => {
    setToggling(true)
    try {
      await call('toggle_dictation')
    } catch (err) {
      console.error('Toggle dictation failed:', err)
    } finally {
      setToggling(false)
    }
  }, [call])

  const isRecording = recordingState === 'recording'
  const key = statusKeyFor(recordingState, !!lastError)
  const statusColor = STATUS_COLORS[key] ?? STATUS_COLORS.idle
  const statusLabel = STATUS_LABELS[key] ?? 'READY'

  return (
    <div className="animate-fade-in-up mx-auto flex min-h-full w-full max-w-2xl flex-col items-center justify-center gap-5 px-6 py-4">
      <div className="flex items-center gap-1.5">
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: statusColor }}
          aria-hidden
        />
        <span className="text-[11px] font-medium uppercase tracking-wider text-(--text-muted)">
          {statusLabel}
        </span>
      </div>

      <div className="relative">
        {isRecording && (
          <span className="absolute inset-0 rounded-full animate-pulse-ring" />
        )}
        <button
          onClick={handleToggle}
          disabled={toggling}
          aria-label={isRecording ? 'Stop dictation' : 'Start dictation'}
          className={cn(
            'press-scale relative z-10 flex h-21 w-21 items-center justify-center rounded-full',
            'transition-all duration-200 ease-out',
            'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
            'hover:scale-105',
            isRecording
              ? 'bg-black/15 dark:bg-white/18 hover:bg-black/25 dark:hover:bg-white/28'
              : 'bg-destructive animate-glow-pulse hover:shadow-[0_8px_32px_rgba(255,51,51,0.5)]',
          )}
        >
          {isRecording ? (
            <HugeiconsIcon icon={StopIcon} strokeWidth={1.625} className="h-8 w-8 text-white" />
          ) : (
            <HugeiconsIcon icon={Mic02Icon} strokeWidth={1.625} className="h-8 w-8 text-white" />
          )}
        </button>
      </div>

      <p className="flex items-center gap-1.5 text-[13px] text-(--text-muted)">
        <span>Press</span>
        <span className="inline-flex items-center justify-center rounded-md border border-border bg-(--bg-subtle) px-1.75 py-0.75 font-mono text-[11px] font-medium text-(--text-primary) shadow-[0_1px_3px_rgba(0,0,0,0.06),inset_0_1px_0_rgba(255,255,255,0.4)] dark:shadow-[0_1px_3px_rgba(0,0,0,0.15),inset_0_1px_0_rgba(255,255,255,0.06)] leading-none tracking-tight">
          {hotkey}
        </span>
        <span>or click to dictate</span>
      </p>

      {lastText && (
        <div className="w-130 max-w-full rounded-[10px] bg-(--bg-subtle) px-4 py-3">
          <p className="line-clamp-2 overflow-hidden text-ellipsis text-[13px] text-(--text-muted)">
            {lastText}
          </p>
        </div>
      )}

      {stats && (
        <div className="mt-4 w-full">
          <StatCards stats={stats} />
        </div>
      )}

      <ActivityList
        items={recent}
        lineClamp={2}
        title="Recent Activity"
        showViewAll
        onViewAll={() => onNavigate?.('history')}
      />
    </div>
  )
}
