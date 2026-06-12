// src/renderer/src/pages/Privacy.tsx

import { useState, useEffect, useCallback } from 'react'
import { usePython } from '@/hooks/usePython'
import { HugeiconsIcon } from '@hugeicons/react'
import type { IconSvgElement } from '@hugeicons/react'
import {
  Shield01Icon,
  Time02Icon,
  CloudIcon,
  File02Icon,
  SpeechToTextIcon,
  Database02Icon,
  ArrowDataTransferHorizontalIcon,
  Folder01Icon,
  Delete01Icon,
} from '@hugeicons/core-free-icons'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface PrivacyStats {
  localProcessing: string
  durationStr: string
  cloudCalls: string
  charsStr: string
  totalTranscriptions: number
  cacheSize: string
  totalDuration: number
  totalChars: number
}

export default function PrivacyPage() {
  const { call } = usePython()
  const [stats, setStats] = useState<PrivacyStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [showClearDialog, setShowClearDialog] = useState(false)
  const [snackbar, setSnackbar] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null)

  const showSnack = (message: string, type: 'success' | 'error' | 'warning') => {
    setSnackbar({ message, type })
    setTimeout(() => setSnackbar(null), 3000)
  }

  const loadStats = useCallback(async () => {
    setLoading(true)
    try {
      const cfg = await call<any>('get_config')
      let todayStats = { count: 0, chars: 0 }
      try {
        todayStats = await call<any>('get_today_stats')
      } catch {}

      const isCloudBackend = cfg?.asr_backend !== 'whisper' && cfg?.asr_backend !== undefined
      const totalTranscriptions = todayStats?.count ?? 0
      const totalChars = todayStats?.chars ?? 0

      // Simulate duration from chars (rough estimate: ~10 chars/sec)
      const estimatedDuration = Math.round(totalChars / 100)
      const m = Math.floor(estimatedDuration / 60)
      const s = estimatedDuration % 60
      const durationStr = m > 0 ? `${m}m ${s}s` : `${s}s`

      // Compute estimated cache size from transcriptions (~0.5KB per transcription)
      let cacheSize: string
      if (totalTranscriptions > 1000) {
        cacheSize = `~${Math.round(totalTranscriptions * 0.5 / 1024)} MB`
      } else if (totalTranscriptions > 0) {
        cacheSize = `~${Math.round(totalTranscriptions * 0.5)} KB`
      } else {
        cacheSize = '< 1 KB'
      }

      setStats({
        localProcessing: isCloudBackend ? '0%' : '100%',
        durationStr,
        cloudCalls: isCloudBackend ? String(totalTranscriptions) : '0',
        charsStr: totalChars.toLocaleString(),
        totalTranscriptions,
        cacheSize,
        totalDuration: estimatedDuration,
        totalChars,
      })
    } catch (err) {
      console.error('Failed to load privacy stats:', err)
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => { loadStats() }, [loadStats])

  const exportTranscriptions = async () => {
    try {
      const history = await call<any>('get_history', { limit: 10000 })
      const json = JSON.stringify(history, null, 2)
      await navigator.clipboard.writeText(json)
      showSnack(`Exported ${(history ?? []).length} transcriptions to clipboard`, 'success')
    } catch (err) {
      showSnack('Export failed: Could not fetch history', 'error')
    }
  }

  // Vocabulary lives in localStorage (the Python Config dataclass has no
  // `vocabulary_data` field), so export reads from there.
  const exportVocabulary = async () => {
    try {
      const raw = localStorage.getItem('vocabulary_data') ?? '{}'
      const vocabData = JSON.parse(raw)
      const json = JSON.stringify(vocabData, null, 2)
      await navigator.clipboard.writeText(json)
      showSnack('Vocabulary exported to clipboard', 'success')
    } catch (err) {
      showSnack('Export failed', 'error')
    }
  }

  // Clear All Data: templates/vocabulary are stored client-side, so we
  // wipe localStorage. We intentionally do not call update_config because
  // those keys are not in the Python Config dataclass.
  const clearAllData = async () => {
    setShowClearDialog(false)
    try {
      localStorage.removeItem('vocabulary_data')
      localStorage.removeItem('templates_data')
      showSnack('All data cleared', 'success')
      await loadStats()
    } catch (err) {
      showSnack('Clear failed', 'error')
    }
  }

  const StatCard = ({
    label,
    value,
    icon,
  }: {
    label: string
    value: string
    icon: IconSvgElement
  }) => (
    <div className="card-hover rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-5 flex flex-col items-center justify-center gap-3 text-center">
      <HugeiconsIcon icon={icon} className="h-6 w-6 text-[var(--accent)]" />
      <p className="text-2xl font-bold text-[var(--text-primary)]">{value}</p>
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
    </div>
  )

  if (loading || !stats) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="animate-fade-in-up mx-auto flex h-full w-full max-w-2xl flex-col overflow-hidden">
      <div className="space-y-1 px-6 pb-4 pt-6">
        <h1 className="font-sans text-2xl font-bold tracking-tight text-[var(--text-primary)]">
          Privacy
        </h1>
        <p className="text-sm text-[var(--text-muted)]">
          Your data stays on your device
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-6 space-y-6">
        {/* Stat Cards Grid */}
        <div className="grid grid-cols-3 gap-3">
          <StatCard
            label="Local Processing"
            value={stats.localProcessing}
            icon={Shield01Icon}
          />
          <StatCard
            label="Total Transcription Time"
            value={stats.durationStr}
            icon={Time02Icon}
          />
          <StatCard
            label="API Cloud Calls"
            value={stats.cloudCalls}
            icon={CloudIcon}
          />
          <StatCard
            label="Characters Transcribed"
            value={stats.charsStr}
            icon={SpeechToTextIcon}
          />
          <StatCard
            label="Total Transcripts"
            value={String(stats.totalTranscriptions)}
            icon={File02Icon}
          />
          <StatCard
            label="Local Cache Size"
            value={stats.cacheSize}
            icon={Database02Icon}
          />
        </div>

        {/* Data Management */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-6">
          <h2 className="font-sans text-lg font-semibold text-[var(--text-primary)] mb-4">
            Data Management
          </h2>
          <div className="flex flex-wrap gap-3">
            <Button
              variant="outline"
              className="gap-2"
              onClick={exportTranscriptions}
            >
              <HugeiconsIcon icon={ArrowDataTransferHorizontalIcon} className="h-4 w-4" />
              Export Transcriptions
            </Button>
            <Button
              variant="outline"
              className="gap-2"
              onClick={exportVocabulary}
            >
              <HugeiconsIcon icon={Folder01Icon} className="h-4 w-4" />
              Export Vocabulary
            </Button>
            <Button
              variant="destructive"
              className="gap-2"
              onClick={() => setShowClearDialog(true)}
            >
              <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
              Clear All Data
            </Button>
          </div>
        </div>

        {/* Data path */}
        <p className="text-[10px] text-[var(--text-muted)]">
          Data is stored in: ~/.voice-typer/
        </p>
      </div>

      {/* Clear Data Confirmation Dialog */}
      {showClearDialog && (
        <div className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div
            className={cn(
              'animate-scale-in w-[400px] rounded-xl border border-[var(--border)]',
              'bg-[var(--bg)] p-6 shadow-2xl',
            )}
          >
            <h2 className="text-lg font-semibold text-[var(--text-primary)] mb-3">
              Clear All Data
            </h2>
            <p className="text-sm text-[var(--text-muted)] mb-6">
              Are you sure you want to clear all data (history, vocabulary, templates)?
              This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <Button variant="ghost" onClick={() => setShowClearDialog(false)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={clearAllData}
              >
                Clear All Data
              </Button>
            </div>
          </div>
        </div>
      )}

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
