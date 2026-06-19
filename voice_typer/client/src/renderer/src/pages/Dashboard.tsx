import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { HugeiconsIcon } from '@hugeicons/react'
import type { IconSvgElement } from '@hugeicons/react'
import {
  Time02Icon,
  SpeechToTextIcon,
  File02Icon,
  AiBrain03Icon,
  Activity03Icon,
  Calendar01Icon,
  LayoutGridIcon,
  Share08Icon,
} from '@hugeicons/core-free-icons'
import PageHeading from '@/components/PageHeading'
import { StatsShareImage } from '@/components/StatsShareImage'
import { useStatsShare, computeShareStats } from '@/hooks/useStatsShare'
import type { TodayStats, HistoryRecord, Page } from '@/types/ipc'
import type { VoiceTyperConfig } from '@/types/config'
import { Button } from '@/components/ui/button.tsx'

// ── Module-level cache ────────────────────────────────────────────
let _cachedData: DashboardData | null = null

interface DashboardData {
  todayCount: number
  todayChars: number
  todayWordCount: number
  todayDuration: number
  totalCount: number
  totalChars: number
  totalDuration: number
  favoritesCount: number
  model: string
  device: string
  language: string
  dailyActivity: { date: string; count: number; label: string; dayName: string }[]
  currentStreak: number
  maxStreak: number
  activeDays: number
}

/** Format seconds into a human-readable duration string. */
function formatDuration(seconds: number): string {
  if (seconds <= 0) return '0m'
  const totalMinutes = Math.round(seconds / 60)
  if (totalMinutes < 60) return `${totalMinutes}m`
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

/** Format number compactly (e.g., 1234 → "1.2K") */
function compactNumber(n: number): string {
  if (n >= 1000) {
    const k = n / 1000
    const display = Math.floor(k * 10) / 10
    return `${display}K`
  }
  return String(n)
}

/** Determine the max bar height based on data range. */
function barHeight(count: number, max: number): number {
  if (max === 0) return 8
  return Math.max(8, Math.round((count / max) * 64))
}

/** Parse a timestamp string to a YYYY-MM-DD date key. */
function dateKey(ts: string): string {
  try { return new Date(ts).toISOString().slice(0, 10) } catch { return ts }
}

/** Get day-of-week abbreviation for a date string. */
function dayAbbr(dateStr: string): string {
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  try { return days[new Date(dateStr).getDay()] } catch { return dateStr }
}

/** Get a human-friendly label like "Today", "Yesterday", or the date. */
function dayLabel(dateStr: string): string {
  try {
    const d = new Date(dateStr)
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(yesterday.getDate() - 1)
    if (dateStr === today.toISOString().slice(0, 10)) return 'Today'
    if (dateStr === yesterday.toISOString().slice(0, 10)) return 'Yesterday'
    return dateStr.slice(5) // "MM-DD"
  } catch { return dateStr }
}

/** Build the 7-day activity array from a list of history records. */
function computeDailyActivity(records: HistoryRecord[]): { date: string; count: number; label: string; dayName: string }[] {
  const counts = new Map<string, number>()
  for (const r of records) {
    const key = dateKey(r.timestamp)
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const result: { date: string; count: number; label: string; dayName: string }[] = []
  const now = new Date()
  for (let i = 6; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    result.push({
      date: key,
      count: counts.get(key) ?? 0,
      label: dayLabel(key),
      dayName: dayAbbr(key),
    })
  }
  return result
}

/** Compute consecutive-day streak from history records. */
function computeStreaks(records: HistoryRecord[]): { current: number; max: number; activeDays: number } {
  const days = new Set<string>()
  for (const r of records) {
    days.add(dateKey(r.timestamp))
  }
  const sorted = Array.from(days).sort().reverse()
  if (sorted.length === 0) return { current: 0, max: 0, activeDays: 0 }

  const today = new Date().toISOString().slice(0, 10)
  const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10)

  // Current streak (must include today or yesterday)
  let current = 0
  if (sorted[0] === today || sorted[0] === yesterday) {
    for (let i = 0; i < sorted.length; i++) {
      const expected = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10)
      if (sorted[i] === expected) current++
      else break
    }
  }

  // Max streak (scan all)
  let max = 1
  let run = 1
  for (let i = 1; i < sorted.length; i++) {
    const prev = new Date(sorted[i - 1])
    const curr = new Date(sorted[i])
    const diffMs = prev.getTime() - curr.getTime()
    if (diffMs <= 86400000 * 1.5) {
      run++
      if (run > max) max = run
    } else {
      run = 1
    }
  }
  if (sorted.length === 1) max = 1

  return { current, max, activeDays: sorted.length }
}

// ── Page Component ────────────────────────────────────────────────

interface DashboardPageProps {
  onNavigate?: (page: Page) => void
}

export default function DashboardPage({ onNavigate }: DashboardPageProps) {
  const { call } = usePython()
  const [data, setData] = useState<DashboardData | null>(_cachedData)
  const [loading, setLoading] = useState(true)
  const [configRaw, setConfigRaw] = useState<VoiceTyperConfig | null>(null)
  const { imageRef, shareAsImage } = useStatsShare()


  /** Fetch all dashboard data from the Python backend. */
  const refreshData = useCallback(async () => {
    try {
      const [cfg, todayStats, history] = await Promise.all([
        call<VoiceTyperConfig>('get_config'),
        call<TodayStats>('get_today_stats').catch(() => ({ count: 0, chars: 0, word_count: 0, duration: 0 })),
        call<HistoryRecord[]>('get_history', { limit: 10000 }).catch(() => [] as HistoryRecord[]),
      ])

      const recs = history ?? []
      const dailyActivity = computeDailyActivity(recs)
      const streaks = computeStreaks(recs)
      const favoritesCount = recs.filter(r => r.favorite > 0).length

      // Total all-time stats
      let totalChars = 0, totalDuration = 0
      for (const r of recs) {
        totalChars += r.char_count ?? 0
        totalDuration += r.duration ?? 0
      }

      const newData: DashboardData = {
        todayCount: todayStats?.count ?? 0,
        todayChars: todayStats?.chars ?? 0,
        todayWordCount: todayStats?.word_count ?? 0,
        todayDuration: todayStats?.duration ?? 0,
        totalCount: recs.length,
        totalChars,
        totalDuration,
        favoritesCount,
        model: cfg?.model_size ?? 'Unknown',
        device: cfg?.device ?? 'Unknown',
        language: cfg?.language || 'Auto',
        dailyActivity,
        currentStreak: streaks.current,
        maxStreak: streaks.max,
        activeDays: streaks.activeDays,
      }
      _cachedData = newData
      setData(newData)
      setConfigRaw(cfg ?? null)
    } catch {
      // Silently ignore — next load picks up fresh data
    }
  }, [call])

  const loadData = useCallback(async () => {
    setLoading(true)
    await refreshData()
    setLoading(false)
  }, [refreshData])

  // ── Proactive background refresh after new transcriptions ────────
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  usePythonEvent('transcription_final', useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(refreshData, 500)
  }, [refreshData]))

  useEffect(() => {
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])



  // ── Stat Card Component ──────────────────────────────────────────

  const StatCard = ({
    label,
    value,
    icon,
    sublabel,
  }: {
    label: string
    value: string
    icon: IconSvgElement
    sublabel?: string
  }) => (
    <div className="card-hover rounded-xl border border-border bg-(--bg-subtle) p-5 flex flex-col items-center justify-center gap-2 text-center">
      <HugeiconsIcon icon={icon} strokeWidth={1.625} className="h-4 w-4 text-accent" />
      <p className="text-2xl font-bold text-(--text-primary) leading-none tracking-tight">{value}</p>
      <p className="text-xs text-(--text-muted)">{label}</p>
      {sublabel && (
        <p className="text-[10px] text-(--text-muted) opacity-60">{sublabel}</p>
      )}
    </div>
  )

  // ── Loading State ────────────────────────────────────────────────

  if (!_cachedData && !data) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      </div>
    )
  }

  // ── Render ───────────────────────────────────────────────────────

  const d = data!
  const maxCount = Math.max(1, ...d.dailyActivity.map(a => a.count))

  return (
    <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
      <PageHeading
        title="Analytics"
        description="Your voice typing activity and usage insights."
      >
        {data && configRaw && data.todayCount > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => shareAsImage('voice-typer-stats')}
            className="gap-2"
          >
            <HugeiconsIcon icon={Share08Icon} strokeWidth={1.625} className="h-4 w-4 shrink-0" />
            Share Stats
          </Button>
        )}
      </PageHeading>

      <div className="space-y-8">
        {/* ── Today's Stats Grid ──────────────────────────────────── */}
        <div className="grid grid-cols-4 gap-3">
          <StatCard
            label="Dictations Today"
            value={String(d.todayCount)}
            icon={SpeechToTextIcon}
            sublabel={`${d.todayChars.toLocaleString()} chars`}
          />
          <StatCard
            label="Recording Time"
            value={formatDuration(d.todayDuration)}
            icon={Time02Icon}
            sublabel="Today"
          />
          <StatCard
            label="All-time Total"
            value={compactNumber(d.totalCount)}
            icon={File02Icon}
            sublabel={`${d.totalChars.toLocaleString()} chars`}
          />
          <StatCard
            label="Active Days"
            value={String(d.activeDays)}
            icon={Calendar01Icon}
            sublabel={d.currentStreak > 0 ? `${d.currentStreak}-day streak` : 'No streak yet'}
          />
        </div>

        {/* ── 7-Day Activity Bar Chart ──────────────────────────────── */}
        <div className="rounded-xl border border-border bg-(--bg-subtle) p-5">
          <div className="flex items-center justify-between mb-5">
            <div className="space-y-0.5">
              <h2 className="font-sans text-sm font-semibold text-(--text-primary)">7-Day Activity</h2>
              <p className="text-xs text-(--text-muted)">Transcriptions per day</p>
            </div>
            <HugeiconsIcon icon={Activity03Icon} strokeWidth={1.625} className="h-4 w-4 text-(--text-muted)" />
          </div>
          <div className="flex items-end justify-between gap-2 h-20">
            {d.dailyActivity.map((day) => (
              <div key={day.date} className="flex flex-1 flex-col items-center gap-2">
                <span className="text-[10px] text-(--text-muted) font-medium tabular-nums">{day.count}</span>
                <div
                  className="w-full max-w-10 rounded-sm bg-accent/60 transition-all duration-300"
                  style={{ height: `${barHeight(day.count, maxCount)}px` }}
                  title={`${day.label}: ${day.count} transcription${day.count !== 1 ? 's' : ''}`}
                />
                <span className="text-[9px] text-(--text-muted) opacity-70">{day.dayName}</span>
              </div>
            ))}
          </div>
        </div>

        {/* ── Quick Stats Bar ──────────────────────────────────────── */}
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-lg border border-border bg-(--bg-subtle) p-3.5 flex items-center gap-3">
            <div className="rounded-lg bg-accent/10 p-2">
              <HugeiconsIcon icon={AiBrain03Icon} strokeWidth={1.625} className="h-4 w-4 text-accent" />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] text-(--text-muted) font-medium">Model</p>
              <p className="text-sm font-semibold text-(--text-primary) truncate">{d.model}</p>
            </div>
          </div>
          <div className="rounded-lg border border-border bg-(--bg-subtle) p-3.5 flex items-center gap-3">
            <div className="rounded-lg bg-accent/10 p-2">
              <HugeiconsIcon icon={LayoutGridIcon} strokeWidth={1.625} className="h-4 w-4 text-accent" />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] text-(--text-muted) font-medium">Device</p>
              <p className="text-sm font-semibold text-(--text-primary) truncate">{d.device.toUpperCase()}</p>
            </div>
          </div>
          <div className="rounded-lg border border-border bg-(--bg-subtle) p-3.5 flex items-center gap-3">
            <div className="rounded-lg bg-accent/10 p-2">
              <HugeiconsIcon icon={Activity03Icon} strokeWidth={1.625} className="h-4 w-4 text-accent" />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] text-(--text-muted) font-medium">Language</p>
              <p className="text-sm font-semibold text-(--text-primary) truncate">{d.language}</p>
            </div>
          </div>
        </div>

        {/* Data path */}
        <p className="text-[10px] text-(--text-muted) text-center pb-4">
          Data stored in: ~/.voice-typer/
        </p>
      </div>

      {/* ── Hidden share image capture target ──────────────── */}
      <div ref={imageRef} style={{ position: 'fixed', left: -9999, top: 0 }}>
        {data && configRaw && (
          <StatsShareImage
            stats={computeShareStats(
              { count: data.todayCount, chars: data.todayChars, word_count: data.todayWordCount, duration: data.todayDuration },
              configRaw.asr_backend,
            )}
          />
        )}
      </div>
    </div>
  )
}
