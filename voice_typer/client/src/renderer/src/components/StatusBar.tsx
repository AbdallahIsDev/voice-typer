// src/renderer/src/components/StatusBar.tsx

import { cn } from '@/lib/utils'
import type { RecordingState } from '@/types/ipc'

interface StatusBarProps {
  connectionStatus: 'connected' | 'disconnected' | 'connecting'
  recordingState: RecordingState
}

const RECORDING_LABELS: Record<RecordingState, string> = {
  idle: 'Ready',
  listening: 'Listening',
  recording: 'Recording',
  processing: 'Transcribing',
  error: 'Error',
}

export function StatusBar({ connectionStatus, recordingState }: StatusBarProps) {
  const isConnected = connectionStatus === 'connected'
  const isRecording = recordingState === 'recording'

  return (
    <footer
      className={cn(
        'flex h-8 shrink-0 items-center justify-between px-4',
        'border-t border-[var(--border)] bg-[var(--bg-subtle)]',
        'transition-colors duration-300',
        isRecording && 'border-t-[var(--accent)]',
      )}
    >
      {/* Connection */}
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'inline-block h-1.5 w-1.5 rounded-full',
            connectionStatus === 'connected' && 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]',
            connectionStatus === 'disconnected' && 'bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.4)]',
            connectionStatus === 'connecting' && 'bg-yellow-500 animate-pulse',
          )}
        />
        <span className="text-[11px] text-[var(--text-muted)]">
          {connectionStatus === 'connected' && 'Python connected'}
          {connectionStatus === 'disconnected' && 'Python disconnected'}
          {connectionStatus === 'connecting' && 'Connecting to Python...'}
        </span>
      </div>

      {/* Recording state */}
      <div className="flex items-center gap-2">
        {isRecording && (
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-[var(--accent)] animate-pulse" />
        )}
        <span
          className={cn(
            'text-[11px]',
            isConnected ? 'text-[var(--text-muted)]' : 'text-[var(--text-muted)] opacity-50',
            isRecording && 'text-[var(--accent)]',
            recordingState === 'error' && 'text-red-400',
          )}
        >
          {RECORDING_LABELS[recordingState]}
        </span>
      </div>
    </footer>
  )
}
