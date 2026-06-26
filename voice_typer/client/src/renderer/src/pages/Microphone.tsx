import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { useSnackbar } from '@/hooks/useSnackbar'
import { HugeiconsIcon } from '@hugeicons/react'
import { Mic02Icon, MicOff01Icon, PlayIcon, StopIcon } from '@hugeicons/core-free-icons'
import PageHeading from '@/components/PageHeading'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { VoiceTyperConfig, MicrophoneDevice } from '@/types/config'
import { Spinner } from '@/components/Spinner'
import { LevelBar } from '@/components/LevelBar'
import { AudioPresetSelector, type AudioPreset, type NoiseFilterState } from '@/components/AudioPresetSelector'
import { MicrophoneListItem } from '@/components/MicrophoneListItem'
import { LiveQualityFeedback } from '@/components/LiveQualityFeedback'
import { TestReviewPanel } from '@/components/TestReviewPanel'

// Module-level cache — persists across page navigations so microphone settings
// render instantly on re-visit instead of showing a loading spinner.
let _cachedMicrophones: MicrophoneDevice[] = []
let _cachedConfig: VoiceTyperConfig | null = null

const PRESET_TO_FILTERS: Record<AudioPreset, Partial<NoiseFilterState>> = {
  none: {
    noise_filter_enabled: false,
    noise_filter_highpass: false,
    noise_filter_gate: false,
    noise_filter_rnnoise: false,
    noise_filter_post_capture: false,
  },
  recommended: {
    noise_filter_enabled: true,
    noise_filter_highpass: false,
    noise_filter_gate: false,
    noise_filter_rnnoise: true,
    noise_filter_post_capture: false,
  },
  noisy_room: {
    noise_filter_enabled: true,
    noise_filter_highpass: true,
    noise_filter_gate: true,
    noise_filter_rnnoise: true,
    noise_filter_post_capture: false,
  },
  studio: {
    noise_filter_enabled: true,
    noise_filter_highpass: true,
    noise_filter_gate: false,
    noise_filter_rnnoise: false,
    noise_filter_post_capture: false,
  },
  custom: {},
}

interface TestResultQuality {
  volume_level: 'good' | 'low' | 'very_low'
  volume_rms: number
  peak_level: number
  noise_level: 'low' | 'moderate' | 'high'
  has_voice: boolean
  has_clipping: boolean
  detected_issues: string[]
  estimated_transcription_quality: number
  silence_ratio: number
}

interface TestStopResult {
  success: boolean
  audio_base64: string
  raw_audio_base64: string
  duration_ms: number
  sample_rate: number
  message: string
  quality: TestResultQuality
}

export default function MicrophonePage() {
  const { call } = usePython()
  const [microphones, setMicrophones] = useState<MicrophoneDevice[]>(_cachedMicrophones)
  const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig)
  const [loading, setLoading] = useState(true)
  const [testRunning, setTestRunning] = useState(false)
  const [testCountdown, setTestCountdown] = useState(0)
  const [testElapsed, setTestElapsed] = useState(0)
  const [testAudioBase64, setTestAudioBase64] = useState<string | null>(null)
  const [rawAudioBase64, setRawAudioBase64] = useState<string | null>(null)
  const [testDurationMs, setTestDurationMs] = useState(0)
  const [testQuality, setTestQuality] = useState<TestResultQuality | null>(null)
  const [level, setLevel] = useState(0)
  const [peak, setPeak] = useState(0)
  const [micMonitoring, setMicMonitoring] = useState(false)

  // Preset + filter state
  const [audioPreset, setAudioPreset] = useState<AudioPreset>(() => {
    return (_cachedConfig?.audio_preset as AudioPreset) ?? 'recommended'
  })
  const [filters, setFilters] = useState<NoiseFilterState>(() => {
    const cfg = _cachedConfig
    return {
      noise_filter_enabled: cfg?.noise_filter_enabled ?? true,
      noise_filter_highpass: cfg?.noise_filter_highpass ?? true,
      noise_filter_gate: cfg?.noise_filter_gate ?? true,
      noise_filter_rnnoise: cfg?.noise_filter_rnnoise ?? false,
      noise_filter_post_capture: cfg?.noise_filter_post_capture ?? true,
    }
  })
  const [showAdvanced, setShowAdvanced] = useState(false)

  // Tracks whether filters have changed since last test (invalidation)
  const [filtersSinceLastTest, setFiltersSinceLastTest] = useState<string>('')
  const { showSnack, Snackbar } = useSnackbar()
  const levelIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const elapsedTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playingEnhanced, setPlayingEnhanced] = useState(false)
  const [playingOriginal, setPlayingOriginal] = useState(false)
  const playingRef = useRef(false)
  const stopTestRef = useRef<() => Promise<void>>(async () => {})
  const stoppingRef = useRef(false)

  const computeFilterKey = useCallback((p: AudioPreset, f: NoiseFilterState): string => {
    return `${p}|${JSON.stringify(f)}`
  }, [])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [mics, cfg] = await Promise.all([
        call<MicrophoneDevice[]>('get_microphones'),
        call<VoiceTyperConfig>('get_config'),
      ])
      _cachedMicrophones = Array.isArray(mics) ? mics : []
      _cachedConfig = cfg
      setMicrophones(_cachedMicrophones)
      setConfig(cfg)
      const preset = (cfg?.audio_preset as AudioPreset) ?? 'recommended'
      setAudioPreset(preset)
      setFilters({
        noise_filter_enabled: cfg?.noise_filter_enabled ?? true,
        noise_filter_highpass: cfg?.noise_filter_highpass ?? true,
        noise_filter_gate: cfg?.noise_filter_gate ?? true,
        noise_filter_rnnoise: cfg?.noise_filter_rnnoise ?? false,
        noise_filter_post_capture: cfg?.noise_filter_post_capture ?? true,
      })
    } catch (err) {
      console.error('Failed to load microphone data:', err)
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => { loadData() }, [loadData])

  // Start continuous level monitoring on mount, stop on unmount
  useEffect(() => {
    const micId = config?.microphone ?? null
    call<{success: boolean}>('level_monitor_start', { mic_id: micId }).catch(() => {})

    levelIntervalRef.current = setInterval(async () => {
      if (playingRef.current) return
      try {
        const levelData = await call<{level: number; peak: number; active: boolean}>(
          'microphone_test_get_level',
        )
        if (levelData && typeof levelData.level === 'number') {
          setLevel(levelData.level)
        }
        if (levelData && typeof levelData.peak === 'number') {
          setPeak(levelData.peak)
        }
        if (levelData && typeof levelData.active === 'boolean') {
          setMicMonitoring(levelData.active)
        }
      } catch {
        // Ignore polling errors
      }
    }, 100)

    return () => {
      if (levelIntervalRef.current) {
        clearInterval(levelIntervalRef.current)
        levelIntervalRef.current = null
      }
      call('level_monitor_stop').catch(() => {})
    }
  }, [call])

  usePythonEvent('microphone_test_complete', useCallback((_data: unknown) => {
    if (testRunning && !stoppingRef.current) {
      stopTestRef.current()
    }
  }, [testRunning]))

  useEffect(() => {
    return () => {
      if (testTimerRef.current) {
        clearInterval(testTimerRef.current)
        testTimerRef.current = null
      }
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current)
        elapsedTimerRef.current = null
      }
      if (testRunning && !stoppingRef.current) {
        call('microphone_test_cancel').catch(() => {})
      }
    }
  }, [call, testRunning])

  // ── Derived state ─────────────────────────────────────────────

  const activeMicId = config?.microphone ?? null
  const isSystemDefault = activeMicId === null
  const activeMicName = activeMicId === null
    ? 'System Default'
    : microphones.find((m) => (m.id ?? String(m.index)) === activeMicId)?.name ?? 'Unknown'
  const otherMicrophones = microphones
    .filter((mic) => (mic.id ?? String(mic.index)) !== activeMicId)
    .sort((a, b) => (a.default ? -1 : b.default ? 1 : 0))

  const filtersChangedSinceTest = filtersSinceLastTest && filtersSinceLastTest !== computeFilterKey(audioPreset, filters)
  const hasFiltersEnabled = filters.noise_filter_enabled

  // ── Handlers ──────────────────────────────────────────────────

  const selectMicrophone = async (micId: string | null) => {
    // Stop any active test first
    if (testRunning && !stoppingRef.current) {
      try {
        await call('microphone_test_cancel')
      } catch { /* ignore */ }
      setTestRunning(false)
      setTestAudioBase64(null)
      setRawAudioBase64(null)
      setTestQuality(null)
      if (testTimerRef.current) {
        clearInterval(testTimerRef.current)
        testTimerRef.current = null
      }
      if (elapsedTimerRef.current) {
        clearInterval(elapsedTimerRef.current)
        elapsedTimerRef.current = null
      }
    }

    setTestAudioBase64(null)
    setRawAudioBase64(null)
    setTestQuality(null)

    try {
      await call('set_config', { microphone: micId })
      setConfig((prev) => (prev ? { ...prev, microphone: micId } : prev))
      setLevel(0)
      setPeak(0)
      setMicMonitoring(false)
      call('level_monitor_start', { mic_id: micId }).catch(() => {})
      const label = micId === null ? 'System Default' : microphones.find((m) => (m.id ?? String(m.index)) === micId)?.name ?? 'Microphone'
      showSnack(`Using: ${label}`, 'success')
    } catch {
      showSnack('Failed to set microphone', 'error')
    }
  }

  const handlePresetChange = useCallback(async (preset: AudioPreset) => {
    setAudioPreset(preset)
    const presetFilters = PRESET_TO_FILTERS[preset]
    if (Object.keys(presetFilters).length > 0) {
      setFilters((prev) => ({ ...prev, ...presetFilters }))
    }
    try {
      await call('set_config', { audio_preset: preset })
      // Also apply individual filter toggles for presets that set them
      if (Object.keys(presetFilters).length > 0) {
        await call('set_config', presetFilters)
      }
    } catch { /* ignore */ }
  }, [call])

  const handleFilterChange = useCallback((key: keyof NoiseFilterState, value: boolean) => {
    setFilters((prev) => ({ ...prev, [key]: value }))
    call('set_config', { [key]: value }).catch(() => {})
  }, [call])

  const startTest = async () => {
    setTestAudioBase64(null)
    setRawAudioBase64(null)
    setTestDurationMs(0)
    setTestQuality(null)
    setLevel(0)
    setPeak(0)
    setPlayingEnhanced(false)
    setPlayingOriginal(false)
    setTestElapsed(0)

    const micId = config?.microphone ?? null

    // Record the current filter state for invalidation tracking
    setFiltersSinceLastTest(computeFilterKey(audioPreset, filters))

    try {
      const result = await call<{ success: boolean; message: string; duration: number; sample_rate: number }>(
        'microphone_test_start',
        {
          mic_id: micId,
          duration: 10,
          filters: filters.noise_filter_enabled ? {
            noise_filter_enabled: true,
            noise_filter_highpass: filters.noise_filter_highpass,
            noise_filter_gate: filters.noise_filter_gate,
            noise_filter_rnnoise: filters.noise_filter_rnnoise,
            noise_filter_post_capture: filters.noise_filter_post_capture,
          } : { noise_filter_enabled: false },
        },
      )

      if (!result?.success) {
        showSnack(result?.message ?? 'Failed to start microphone test', 'error')
        return
      }

      setTestRunning(true)
      setTestCountdown(Math.ceil(result.duration || 10))

      // Timer countdown
      if (testTimerRef.current) clearInterval(testTimerRef.current)
      const startTime = Date.now()
      const totalDurationMs = (result.duration || 10) * 1000
      const checkInterval = setInterval(() => {
        const elapsed = Date.now() - startTime
        const remaining = Math.max(0, Math.ceil((totalDurationMs - elapsed) / 1000))
        setTestCountdown(remaining)

        if (remaining <= 0) {
          clearInterval(checkInterval)
          if (checkInterval === testTimerRef.current) {
            testTimerRef.current = null
          }
          stopTestRef.current()
        }
      }, 500)
      testTimerRef.current = checkInterval

      // Elapsed timer for the 00:03 / 00:10 display
      if (elapsedTimerRef.current) clearInterval(elapsedTimerRef.current)
      const elapsedInterval = setInterval(() => {
        const elapsed = Date.now() - startTime
        setTestElapsed(Math.floor(elapsed / 1000))
      }, 200)
      elapsedTimerRef.current = elapsedInterval

    } catch (err) {
      console.error('Failed to start microphone test:', err)
      showSnack('Failed to start microphone test', 'error')
    }
  }

  const stopTest = async () => {
    if (stoppingRef.current) return
    stoppingRef.current = true

    setTestRunning(false)
    if (testTimerRef.current) {
      clearInterval(testTimerRef.current)
      testTimerRef.current = null
    }
    if (elapsedTimerRef.current) {
      clearInterval(elapsedTimerRef.current)
      elapsedTimerRef.current = null
    }
    setLevel(0)
    setTestCountdown(0)

    try {
      const result = await call<TestStopResult>('microphone_test_stop')

      if (result?.success && result?.audio_base64) {
        setTestAudioBase64(result.audio_base64)
        setRawAudioBase64(result.raw_audio_base64 || null)
        setTestDurationMs(result.duration_ms || 0)
        if (result.quality) {
          setTestQuality(result.quality)
        }
        showSnack(`${(result.duration_ms / 1000).toFixed(1)}s recorded`, 'success')
      } else if (result?.success) {
        let msg = 'No audio detected.'
        if (activeMicId !== null) {
          msg += ' Try the default microphone.'
        }
        showSnack(msg, 'warning')
      } else {
        showSnack(result?.message ?? 'Test failed', 'error')
      }
    } catch (err) {
      console.error('Failed to stop microphone test:', err)
      showSnack('Failed to stop microphone test', 'error')
    } finally {
      stoppingRef.current = false
    }
  }

  stopTestRef.current = stopTest

  const playAudio = (base64: string, isEnhanced: boolean) => {
    if (!base64) return
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }

    if (isEnhanced) {
      setPlayingEnhanced(true)
      setPlayingOriginal(false)
    } else {
      setPlayingEnhanced(false)
      setPlayingOriginal(true)
    }
    playingRef.current = true

    try {
      const audioDataUri = `data:audio/wav;base64,${base64}`
      const audio = new Audio(audioDataUri)
      audioRef.current = audio

      audio.onended = () => {
        setPlayingEnhanced(false)
        setPlayingOriginal(false)
        playingRef.current = false
        audioRef.current = null
      }

      audio.onerror = () => {
        setPlayingEnhanced(false)
        setPlayingOriginal(false)
        playingRef.current = false
        audioRef.current = null
        showSnack('Could not play the test recording.', 'error')
      }

      audio.play().catch(() => {
        setPlayingEnhanced(false)
        setPlayingOriginal(false)
        playingRef.current = false
        audioRef.current = null
        showSnack('Playback failed. Try again.', 'error')
      })
    } catch {
      setPlayingEnhanced(false)
      setPlayingOriginal(false)
      playingRef.current = false
      showSnack('Failed to start playback.', 'error')
    }
  }

  const stopPlayback = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setPlayingEnhanced(false)
    setPlayingOriginal(false)
    playingRef.current = false
  }

  // ── Render ────────────────────────────────────────────────────

  if (!_cachedMicrophones.length && !_cachedConfig && loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
      <PageHeading
        title="Microphone"
        description="Select and test your microphone"
      />

      <div className="space-y-6">
        {/* Active Microphone Card */}
        <div
          className={cn(
            'rounded-xl border p-5 transition-colors',
            'border-accent bg-(--bg-subtle)',
          )}
        >
          {/* Mic header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <HugeiconsIcon icon={Mic02Icon} strokeWidth={1.625} className="h-4 w-4" />
              <div>
                <p className="text-sm font-semibold text-(--text-primary)">
                  {activeMicName}
                </p>
                <p className="text-xs text-(--text-muted)">
                  {isSystemDefault
                    ? 'Operating system default input device'
                    : 'Selected microphone'}
                </p>
              </div>
            </div>
            <span className="inline-flex items-center rounded-md px-2.5 py-0.5 text-[10px] font-semibold border border-primary/20 bg-primary/10 text-primary">
              {testRunning ? 'Recording...' : 'Active'}
            </span>
          </div>

          {/* Level bar */}
          <div className="mt-3">
            <LevelBar level={level} playing={playingRef.current} />
          </div>

          {/* Live quality feedback during test */}
          <LiveQualityFeedback
            level={level}
            peak={peak}
            isRecording={testRunning}
            elapsedSeconds={testElapsed}
            totalSeconds={10}
          />

          {/* Test controls */}
          <div className="mt-4 flex items-center gap-3">
            {!testRunning ? (
              <Button
                variant="default"
                size="sm"
                className="gap-2"
                disabled={playingRef.current}
                onClick={startTest}
              >
                <HugeiconsIcon icon={PlayIcon} strokeWidth={1.625} className="h-4 w-4" />
                Start Test
              </Button>
            ) : (
              <Button
                variant="default"
                size="sm"
                className="gap-2 animate-pulse"
                onClick={stopTest}
              >
                <HugeiconsIcon icon={StopIcon} strokeWidth={1.625} className="h-4 w-4" />
                Stop Test ({testCountdown}s)
              </Button>
            )}

            <span className="text-xs text-(--text-muted) ml-auto">
              {testRunning
                ? `Level: ${Math.round(level * 100)}%`
                : testDurationMs > 0
                  ? `Duration: ${(testDurationMs / 1000).toFixed(1)}s`
                  : micMonitoring
                    ? `Level: ${Math.round(level * 100)}%`
                    : 'Monitoring...'
              }
            </span>
          </div>

          {/* Filter invalidation notice */}
          {filtersSinceLastTest && filtersChangedSinceTest && !testRunning && (
            <div className="mt-3 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-500">
              ⚠ Filter settings changed — previous test results no longer reflect current settings. Run a new test.
            </div>
          )}

          {/* Test Review Panel */}
          <TestReviewPanel
            durationMs={testDurationMs}
            quality={testQuality}
            testAudioBase64={testAudioBase64}
            rawAudioBase64={rawAudioBase64}
            playing={playingEnhanced || playingOriginal}
            playingOriginal={playingOriginal}
            onPlayEnhanced={() => playAudio(testAudioBase64!, true)}
            onPlayOriginal={() => rawAudioBase64 ? playAudio(rawAudioBase64, false) : undefined}
            onStop={stopPlayback}
            onRetest={startTest}
            hasFiltersEnabled={hasFiltersEnabled}
          />

          {/* Audio Enhancement / Preset selector */}
          <div className="mt-3">
            <AudioPresetSelector
              preset={audioPreset}
              filters={filters}
              showAdvanced={showAdvanced}
              onPresetChange={handlePresetChange}
              onToggleAdvanced={() => setShowAdvanced((v) => !v)}
              onFilterChange={handleFilterChange}
            />
          </div>
        </div>

        {/* Available Microphones List */}
        {microphones.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <HugeiconsIcon icon={MicOff01Icon} strokeWidth={1.625} className="h-10 w-10 text-(--text-muted) opacity-30" />
            <p className="text-sm text-(--text-muted)">No microphones found</p>
            <p className="text-xs text-(--text-muted) opacity-70">
              Connect a microphone and restart
            </p>
          </div>
        ) : (
          <div>
            <p className="text-xs font-semibold capitalize tracking-wide text-(--text-muted) mb-2 px-1">
              Other Microphones
            </p>
            <div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
              {otherMicrophones.length === 0 ? (
                <div className="px-3.5 py-3 text-xs text-(--text-muted)">
                  No other microphones available
                </div>
              ) : (
                otherMicrophones.map((mic) => (
                  <div key={mic.id ?? String(mic.index)} className={cn(testRunning && 'opacity-50 pointer-events-none')}>
                    <MicrophoneListItem
                      mic={mic}
                      isSystemDefault={isSystemDefault}
                      onSelect={(micId) => selectMicrophone(micId)}
                    />
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>

      <Snackbar />
    </div>
  )
}
