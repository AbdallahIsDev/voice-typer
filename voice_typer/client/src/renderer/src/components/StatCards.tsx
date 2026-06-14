import { Mic, Type, Clock } from 'lucide-react'
import type { TodayStats } from '@/types/ipc'

function formatCompactNumber(n: number): string {
  if (n >= 1000) {
    const k = n / 1000
    const display = Math.floor(k * 10) / 10
    const suffix = n % 1000 > 0 ? 'K+' : 'K'
    if (display === Math.floor(display)) return `${Math.floor(display)}${suffix}`
    return `${display}${suffix}`
  }
  return n.toLocaleString()
}

function formatDuration(seconds: number): string {
  const totalMinutes = Math.max(seconds > 0 ? 1 : 0, Math.round(seconds / 60))
  const h = Math.floor(totalMinutes / 60)
  const m = totalMinutes % 60
  if (totalMinutes === 0) return `0m`
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

const CARDS: {
  label: string
  key: keyof TodayStats
  icon: typeof Mic
  format: (v: number) => string
}[] = [
  { label: 'Voice Dictations', key: 'count', icon: Mic, format: formatCompactNumber },
  { label: 'Text Transcribed', key: 'chars', icon: Type, format: formatCompactNumber },
  { label: 'Dictation Time', key: 'duration', icon: Clock, format: formatDuration },
]

interface StatCardsProps {
  stats: TodayStats
}

export default function StatCards({ stats }: StatCardsProps) {
  return (
    <div className="flex gap-2 w-full">
      {CARDS.map((card) => {
        const Icon = card.icon
        return (
          <div key={card.label} className="rounded-lg bg-(--bg-subtle) px-4 py-3 flex-1 border border-border">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Icon className="h-3.5 w-3.5 text-(--text-muted)" />
              <span className="text-[11px] text-(--text-muted) font-medium">
                {card.label}
              </span>
            </div>
            <span className="text-xl font-bold text-(--text-primary) leading-none tracking-tight">
              {card.format(stats[card.key])}
            </span>
          </div>
        )
      })}
    </div>
  )
}
