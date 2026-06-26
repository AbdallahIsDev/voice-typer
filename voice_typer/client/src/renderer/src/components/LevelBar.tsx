import { cn } from '@/lib/utils'

interface LevelBarProps {
  /** RMS level 0–1 */
  level: number
  /** Whether audio playback is active (freezes the bar) */
  playing: boolean
}

function getLevelColor(lvl: number): string {
  if (lvl > 0.7) return 'var(--destructive)'
  if (lvl > 0.3) return 'var(--primary)'
  return 'var(--accent)'
}

export function LevelBar({ level, playing }: LevelBarProps) {
  return (
    <div
      className={cn(
        'h-1.5 w-full rounded-full overflow-hidden transition-opacity duration-200',
        playing ? 'bg-(--text-muted)/10' : 'bg-border',
      )}
      role="progressbar"
      aria-label={
        playing
          ? 'Microphone input level (frozen during playback)'
          : 'Microphone input level'
      }
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(level * 100)}
    >
      <div
        className={cn(
          'h-full rounded-full transition-all duration-75',
          playing && 'opacity-30',
        )}
        style={{
          width: `${Math.max(1, level * 100)}%`,
          backgroundColor: getLevelColor(level),
        }}
      />
    </div>
  )
}
