import { useState, useEffect, useCallback } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import { Mic02Icon, StopIcon } from '@hugeicons/core-free-icons'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { cn } from '@/lib/utils'
import type { RecordingState, TodayStats } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'

interface HomeProps {
  recordingState: RecordingState
  lastError: string | null
}

// FLIT styles.STATUS_COLORS keys we map our RecordingState into.
const STATUS_COLORS: Record<string, string> = {
  idle: '#22C55E',
  recording: '#FF3333',
  processing: '#2563EB',
  loading: '#F59E0B',
  error: '#FF3333',
}

// FLIT styles.STATUS_LABELS for the same keys.
const STATUS_LABELS: Record<string, string> = {
  idle: 'READY',
  recording: 'RECORDING',
  processing: 'TRANSCRIBING',
  loading: 'LOADING',
  error: 'ERROR',
}

function statusKeyFor(state: RecordingState, hasError: boolean): string {
  if (state === 'listening') return 'idle'
  if (state === 'error' && hasError) return 'error'
  return state
}

export default function Home({ recordingState, lastError }: HomeProps) {
  const { call } = usePython()

  const [hotkey, setHotkey] = useState('F2')
  const [lastText, setLastText] = useState('')
  const [stats, setStats] = useState<TodayStats | null>(null)
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
    }
    load()
    return () => {
      cancelled = true
    }
  }, [call])

  usePythonEvent('transcription_final', (data) => {
    if (typeof data?.text === 'string' && data.text.trim()) {
      setLastText(data.text)
    }
  })

  usePythonEvent('recording_started', () => {
    setLastText('')
  })

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
    <div className="animate-fade-in-up mx-auto flex h-full w-full max-w-2xl flex-col items-center gap-5 overflow-y-auto px-6 py-8">
      {/* Status line: 8px dot + label, matches FLIT home.py:44-55 */}
      <div className="flex items-center gap-1.5">
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: statusColor }}
          aria-hidden
        />
        <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
          {statusLabel}
        </span>
      </div>

      {/* Record button: 72x72 circle, matches FLIT home.py:57-64.
          Scale + shadow micro-interactions on hover/active. */}
      <button
        onClick={handleToggle}
        disabled={toggling}
        aria-label={isRecording ? 'Stop dictation' : 'Start dictation'}
        className={cn(
          'press-scale flex h-[72px] w-[72px] items-center justify-center rounded-full',
          'transition-all duration-200 ease-out',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
          'hover:scale-105 hover:shadow-[0_8px_24px_rgba(255,51,51,0.35)]',
          isRecording
            ? 'bg-[rgba(255,255,255,0.18)] hover:bg-[rgba(255,255,255,0.28)] hover:shadow-[0_8px_24px_rgba(255,255,255,0.15)]'
            : 'bg-[#FF3333]',
        )}
      >
        {isRecording ? (
          <HugeiconsIcon icon={StopIcon} className="h-7 w-7 text-white" />
        ) : (
          <HugeiconsIcon icon={Mic02Icon} className="h-7 w-7 text-white" />
        )}
      </button>

      {/* Hotkey hint: "Press [F2] or click to dictate", matches FLIT home.py:66-92 */}
      <p className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
        <span>Press</span>
        <span className="rounded border border-[var(--border)] bg-[var(--bg-subtle)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-primary)]">
          {hotkey}
        </span>
        <span>or click to dictate</span>
      </p>

      {/* Last text preview, matches FLIT home.py:94-107 */}
      {lastText && (
        <div className="w-[520px] max-w-full rounded-[10px] bg-[var(--bg-subtle)] px-4 py-3">
          <p className="line-clamp-2 overflow-hidden text-ellipsis text-[13px] text-[var(--text-muted)]">
            {lastText}
          </p>
        </div>
      )}

      {/* Stats row: 2 cards (Today, Characters), matches FLIT home.py:109-142 */}
      {stats && (
        <div className="mt-2 flex gap-3">
          <div className="flex w-[140px] flex-col items-center gap-1 rounded-[10px] bg-[var(--bg-subtle)] px-6 py-3.5">
            <span className="text-2xl font-semibold text-[var(--accent)]">
              {stats.count}
            </span>
            <span className="text-[11px] text-[var(--text-muted)]">Today</span>
          </div>
          <div className="flex w-[140px] flex-col items-center gap-1 rounded-[10px] bg-[var(--bg-subtle)] px-6 py-3.5">
            <span className="text-2xl font-semibold text-[var(--accent)]">
              {stats.chars.toLocaleString()}
            </span>
            <span className="text-[11px] text-[var(--text-muted)]">Characters</span>
          </div>
        </div>
      )}
    </div>
  )
}
