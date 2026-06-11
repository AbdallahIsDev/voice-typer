import { useState, useEffect, useCallback } from 'react'
import { usePython } from '@/hooks/usePython'
import { HugeiconsIcon } from '@hugeicons/react'
import { HistoryIcon, ClipboardCopyIcon, Delete01Icon } from '@hugeicons/core-free-icons'
import { cn } from '@/lib/utils'
import type { HistoryRecord, TodayStats } from '@/types/ipc'

export default function HistoryPage() {
  const { call } = usePython()
  const [records, setRecords] = useState<HistoryRecord[]>([])
  const [stats, setStats] = useState<TodayStats | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [recs, todayStats] = await Promise.all([
        call<HistoryRecord[]>('get_history', { limit: 100 }),
        call<TodayStats>('get_today_stats'),
      ])
      setRecords(recs)
      setStats(todayStats)
    } catch (err) {
      console.error('Failed to load history:', err)
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => { load() }, [load])

  const handleCopy = (text: string) => navigator.clipboard.writeText(text)

  const formatTime = (ts: string) => {
    const d = new Date(ts.replace(' ', 'T') + 'Z')
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="space-y-1 px-6 pb-4 pt-6">
        <h1 className="font-serif text-2xl font-bold tracking-tight text-[var(--text-primary)]">
          History
        </h1>
        {stats && (
          <p className="text-sm text-[var(--text-muted)]">
            {stats.count} transcription{stats.count !== 1 ? 's' : ''} today
            {stats.chars > 0 && ` (${stats.chars.toLocaleString()} chars)`}
          </p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-4">
        {records.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <HugeiconsIcon icon={HistoryIcon} className="h-8 w-8 text-[var(--text-muted)] opacity-30" />
            <p className="text-sm text-[var(--text-muted)] opacity-50">No transcriptions yet</p>
          </div>
        ) : (
          <div className="space-y-1">
            {records.map((r) => (
              <div
                key={r.id}
                className={cn(
                  'group flex items-start gap-3 rounded-lg px-3 py-2.5',
                  'transition-colors hover:bg-[var(--surface-hover)]',
                )}
              >
                <span className="shrink-0 pt-0.5 font-mono text-[10px] text-[var(--text-muted)] min-w-[100px]">
                  {formatTime(r.timestamp)}
                </span>
                <p className="flex-1 text-sm leading-relaxed text-[var(--text-primary)]">
                  {r.text}
                </p>
                <span className="shrink-0 text-[10px] text-[var(--text-muted)] pt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  {r.model}{r.device ? `/${r.device}` : ''}
                </span>
                <button
                  onClick={() => handleCopy(r.text)}
                  className="shrink-0 rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                  title="Copy"
                >
                  <HugeiconsIcon icon={ClipboardCopyIcon} className="h-3 w-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {records.length > 0 && (
          <button
            onClick={load}
            className="mt-4 w-full py-2 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
          >
            Refresh
          </button>
        )}
      </div>
    </div>
  )
}
