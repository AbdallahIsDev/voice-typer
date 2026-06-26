import { cn } from '@/lib/utils'

interface LiveQualityFeedbackProps {
  level: number
  peak: number
  isRecording: boolean
  elapsedSeconds: number
  totalSeconds: number
}

export function LiveQualityFeedback({
  level,
  peak,
  isRecording,
  elapsedSeconds,
  totalSeconds,
}: LiveQualityFeedbackProps) {
  if (!isRecording) return null

  const hasVoice = peak > 0.05
  const volumeGood = level > 0.02 && level < 0.7
  const volumeLow = level <= 0.02 && level > 0.005
  const volumeVeryLow = level <= 0.005
  const tooLoud = peak > 0.9

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  }

  return (
    <div className="mt-2 space-y-2">
      {/* Timer */}
      <div className="text-center">
        <span className="text-xs font-mono tabular-nums text-(--text-muted)">
          Recording... {formatTime(elapsedSeconds)} / {formatTime(totalSeconds)}
        </span>
      </div>

      {/* Voice detected indicator */}
      <div className="flex items-center justify-center gap-3 text-xs">
        <span className="flex items-center gap-1.5">
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full animate-pulse',
              hasVoice ? 'bg-green-500 shadow-[0_0_4px_rgba(34,197,94,0.6)]' : 'bg-(--text-muted)/30',
            )}
          />
          {hasVoice ? '✓ Voice detected' : 'Waiting for voice...'}
        </span>

        {/* Quality indicator */}
        {hasVoice && !tooLoud && (
          <span className="text-green-500">
            Quality: Excellent
          </span>
        )}
        {hasVoice && tooLoud && (
          <span className="text-amber-500">
            ⚠ Volume too high (clipping risk)
          </span>
        )}
        {volumeVeryLow && !hasVoice && (
          <span className="text-amber-500">
            ⚠ Volume too low
          </span>
        )}
        {volumeLow && !hasVoice && (
          <span className="text-amber-500">
            ⚠ Low volume — speak up
          </span>
        )}
      </div>
    </div>
  )
}
