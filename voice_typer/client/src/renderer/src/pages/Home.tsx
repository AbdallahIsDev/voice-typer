// src/renderer/src/pages/Home.tsx

import { useState, useEffect, useRef, useCallback } from 'react'
import { HugeiconsIcon } from "@hugeicons/react"
import { Mic02Icon, MicOff01Icon, Loading03Icon, Copy01Icon, Delete01Icon, ClipboardCopyIcon } from "@hugeicons/core-free-icons"
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { cn } from '@/lib/utils'
import type { RecordingState } from '@/types/ipc'

interface Transcript {
  id: number
  text: string
  timestamp: Date
}

interface HomeProps {
  recordingState: RecordingState
  lastError: string | null
}

export default function Home({ recordingState, lastError }: HomeProps) {
  const { call } = usePython()

  const [transcripts, setTranscripts] = useState<Transcript[]>([])
  const [partialTranscript, setPartialTranscript] = useState('')
  const [toggling, setToggling] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)

  // ── Event subscriptions ───────────────────────────────────────

  usePythonEvent('transcription_partial', (data) => {
    if (typeof data?.text === 'string') {
      setPartialTranscript(data.text)
    }
  })

  usePythonEvent('transcription_final', (data) => {
    if (typeof data?.text === 'string' && data.text.trim()) {
      setTranscripts((prev) => [
        ...prev,
        { id: Date.now(), text: data.text as string, timestamp: new Date() },
      ])
      setPartialTranscript('')
    }
  })

  usePythonEvent('recording_started', () => {
    setPartialTranscript('')
  })

  usePythonEvent('recording_stopped', () => {
    // Keep partial until final arrives
  })

  // ── Auto-scroll ───────────────────────────────────────────────

  useEffect(() => {
    const el = scrollRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [transcripts, partialTranscript])

  // ── Handlers ──────────────────────────────────────────────────

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

  const handleCopyTranscript = useCallback((text: string) => {
    navigator.clipboard.writeText(text)
  }, [])

  const handleCopyAll = useCallback(() => {
    const allText = transcripts.map((t) => t.text).join('\n')
    if (allText) navigator.clipboard.writeText(allText)
  }, [transcripts])

  const handleClear = useCallback(() => {
    setTranscripts([])
    setPartialTranscript('')
  }, [])

  // ── Derived state ─────────────────────────────────────────────

  const isActive = recordingState === 'recording' || recordingState === 'listening'
  const isProcessing = recordingState === 'processing'
  const isError = recordingState === 'error'
  const hasTranscripts = transcripts.length > 0 || !!partialTranscript

  // ── Format helpers ────────────────────────────────────────────

  const formatTime = (date: Date) =>
    date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    })

  // ── Status label ──────────────────────────────────────────────

  const statusText = (() => {
    switch (recordingState) {
      case 'idle':
        return 'Ready to listen'
      case 'listening':
        return 'Listening...'
      case 'recording':
        return 'Recording...'
      case 'processing':
        return 'Transcribing...'
      case 'error':
        return lastError ?? 'Something went wrong'
    }
  })()

  return (
    <div className="flex h-full flex-col">
      {/* ── Top: Mic control ──────────────────────────────────── */}
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6">
        {/* Status text */}
        <p
          className={cn(
            'text-sm font-medium transition-colors duration-300',
            isActive && 'text-[var(--accent)]',
            isProcessing && 'text-[var(--accent-muted)]',
            isError && 'text-red-400',
            !isActive && !isProcessing && !isError && 'text-[var(--text-muted)]',
          )}
        >
          {statusText}
        </p>

        {/* Mic button */}
        <div className="relative flex items-center justify-center">
          {/* Pulse rings — only when active */}
          {isActive && (
            <>
              <span
                className={cn(
                  'absolute h-36 w-36 rounded-full',
                  'bg-[var(--accent)] opacity-15',
                  'animate-ping',
                )}
              />
              <span
                className={cn(
                  'absolute -inset-4 rounded-full',
                  'border border-[var(--accent)] opacity-10',
                  'animate-pulse',
                )}
              />
            </>
          )}

          <button
            onClick={handleToggle}
            disabled={toggling || !recordingState}
            className={cn(
              'relative z-10 flex h-36 w-36 items-center justify-center',
              'rounded-full border-2 transition-all duration-300',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
              'focus-visible:ring-offset-4 focus-visible:ring-offset-[var(--bg)]',
              'cursor-pointer select-none',
              // Recording / listening
              isActive &&
                'border-[var(--accent)] bg-[var(--accent)] text-[var(--bg)] shadow-[0_0_30px_rgba(201,162,39,0.25)]',
              // Processing
              isProcessing &&
                'border-[var(--accent-muted)] bg-[var(--accent-soft)] text-[var(--accent)]',
              // Error
              isError &&
                'border-red-500/40 bg-red-500/10 text-red-400',
              // Idle
              !isActive &&
                !isProcessing &&
                !isError &&
                'border-[var(--border)] bg-transparent text-[var(--text-muted)] hover:border-[var(--accent-muted)] hover:text-[var(--text-secondary)]',
            )}
          >
            {isProcessing ? (
              <HugeiconsIcon icon={Loading03Icon} className="h-10 w-10 animate-spin" />
            ) : isError ? (
              <HugeiconsIcon icon={MicOff01Icon} className="h-10 w-10" />
            ) : (
              <HugeiconsIcon icon={Mic02Icon} className="h-10 w-10" />
            )}
          </button>
        </div>

        {/* Hotkey hint */}
        <p className="text-xs text-[var(--text-muted)] opacity-60">
          Press your hotkey or click to toggle
        </p>
      </div>

      {/* ── Bottom: Transcription panel ───────────────────────── */}
      <div
        className={cn(
          'flex flex-col border-t border-[var(--border)]',
          'bg-[var(--bg-subtle)]',
          hasTranscripts ? 'h-[45%] min-h-[200px]' : 'h-auto',
        )}
      >
        {/* Panel header */}
        <div className="flex items-center justify-between px-5 py-2.5">
          <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
            Transcription
          </span>
          {hasTranscripts && (
            <div className="flex items-center gap-1">
              <button
                onClick={handleCopyAll}
                className={cn(
                  'flex items-center gap-1.5 rounded-md px-2 py-1',
                  'text-[11px] text-[var(--text-muted)]',
                  'transition-colors hover:bg-[var(--surface-hover)] hover:text-[var(--text-secondary)]',
                )}
                title="Copy all transcripts"
              >
                <HugeiconsIcon icon={ClipboardCopyIcon} className="h-3 w-3" />
                Copy All
              </button>
              <button
                onClick={handleClear}
                className={cn(
                  'flex items-center gap-1.5 rounded-md px-2 py-1',
                  'text-[11px] text-[var(--text-muted)]',
                  'transition-colors hover:bg-[var(--surface-hover)] hover:text-[var(--text-secondary)]',
                )}
                title="Clear transcripts"
              >
                <HugeiconsIcon icon={Delete01Icon} className="h-3 w-3" />
                Clear
              </button>
            </div>
          )}
        </div>

        {/* Transcripts list */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-2 pb-3">
          {!hasTranscripts ? (
            <div className="flex flex-col items-center justify-center py-12 gap-2">
              <HugeiconsIcon icon={Mic02Icon} className="h-6 w-6 text-[var(--text-muted)] opacity-30" />
              <p className="text-sm text-[var(--text-muted)] opacity-50">
                No transcriptions yet
              </p>
              <p className="text-xs text-[var(--text-muted)] opacity-30">
                Start dictating to see results here
              </p>
            </div>
          ) : (
            <div className="space-y-0.5">
              {/* Final transcripts */}
              {transcripts.map((t) => (
                <div
                  key={t.id}
                  className={cn(
                    'group flex items-start gap-3 rounded-lg px-3 py-2',
                    'transition-colors hover:bg-[var(--surface-hover)]',
                  )}
                >
                  <span className="shrink-0 pt-0.5 font-mono text-[10px] text-[var(--text-muted)]">
                    {formatTime(t.timestamp)}
                  </span>
                  <p className="flex-1 text-sm leading-relaxed text-[var(--text-primary)]">
                    {t.text}
                  </p>
                  <button
                    onClick={() => handleCopyTranscript(t.text)}
                    className={cn(
                      'shrink-0 rounded p-1',
                      'opacity-0 transition-opacity group-hover:opacity-100',
                      'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                    )}
                    title="Copy transcript"
                  >
                    <HugeiconsIcon icon={Copy01Icon} className="h-3 w-3" />
                  </button>
                </div>
              ))}

              {/* Partial transcript (live) */}
              {partialTranscript && (
                <div className="flex items-start gap-3 px-3 py-2">
                  <span className="shrink-0 pt-0.5 font-mono text-[10px] text-[var(--accent)] animate-pulse">
                    live
                  </span>
                  <p className="flex-1 text-sm leading-relaxed text-[var(--text-muted)] italic">
                    {partialTranscript}
                    <span
                      className={cn(
                        'ml-0.5 inline-block h-3.5 w-[2px] align-text-bottom',
                        'bg-[var(--accent)] animate-pulse',
                      )}
                    />
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
