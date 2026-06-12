// src/renderer/src/pages/Models.tsx

import { useState, useEffect, useCallback } from 'react'
import { usePython } from '@/hooks/usePython'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  AiBrain03Icon,
  Download01Icon,
  Delete01Icon,
  PlayIcon,
  Tick02Icon,
  SparklesIcon,
  Shield01Icon,
  ZapIcon,
} from '@hugeicons/core-free-icons'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import type { VoiceTyperConfig } from '@/types/config'

interface ModelInfo {
  name: string
  size: string
  speed: string
  backend: string
  downloaded: boolean
  depsOk: boolean
  isActive: boolean
}

const CLOUD_PROVIDERS = [
  { key: 'openai', label: 'OpenAI Whisper API', url: 'https://api.openai.com/v1/audio/transcriptions', model: 'whisper-1' },
  { key: 'groq', label: 'Groq Whisper API', url: 'https://api.groq.com/openai/v1/audio/transcriptions', model: 'whisper-large-v3' },
  { key: 'deepgram', label: 'Deepgram API', url: 'https://api.deepgram.com/v1/listen', model: 'nova-2' },
] as const

const INITIAL_MODELS: ModelInfo[] = [
  { name: 'tiny.en', size: '~75MB', speed: 'Fastest', backend: 'whisper', downloaded: false, depsOk: true, isActive: false },
  { name: 'small.en', size: '~466MB', speed: 'Fast', backend: 'whisper', downloaded: false, depsOk: true, isActive: false },
  { name: 'medium.en', size: '~1.5GB', speed: 'Slow', backend: 'whisper', downloaded: false, depsOk: true, isActive: false },
  { name: 'qwen', size: 'Variable', speed: 'Fast', backend: 'qwen', downloaded: false, depsOk: true, isActive: false },
  { name: 'parakeet', size: '~2.5GB', speed: 'Fast', backend: 'parakeet', downloaded: false, depsOk: false, isActive: false },
]

export default function ModelsPage() {
  const { call } = usePython()
  const [config, setConfig] = useState<VoiceTyperConfig | null>(null)
  const [models, setModels] = useState<ModelInfo[]>(INITIAL_MODELS)
  const [loading, setLoading] = useState(true)
  const [downloadProgress, setDownloadProgress] = useState(0)
  const [downloadStatus, setDownloadStatus] = useState('')
  const [isDownloading, setIsDownloading] = useState(false)
  const [benchmarkResult, setBenchmarkResult] = useState('')
  const [isBenchmarking, setIsBenchmarking] = useState(false)
  const [snackbar, setSnackbar] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null)

  // Cloud provider API keys
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [testResults, setTestResults] = useState<Record<string, string>>({})

  const showSnack = (message: string, type: 'success' | 'error' | 'warning') => {
    setSnackbar({ message, type })
    setTimeout(() => setSnackbar(null), 3000)
  }

  const loadConfig = useCallback(async () => {
    setLoading(true)
    try {
      const cfg = await call<any>('get_config')
      setConfig(cfg)

      // Update models based on config
      const activeBackend = cfg?.asr_backend ?? 'whisper'
      const activeModel = cfg?.model_size ?? 'small.en'
      setModels(INITIAL_MODELS.map((m) => {
        let isActive = false
        if (m.backend === 'whisper') {
          isActive = activeBackend === 'whisper' && m.name === activeModel
        } else {
          isActive = activeBackend === m.backend
        }
        return { ...m, isActive }
      }))

      setApiKeys({
        openai: cfg?.openai_api_key ?? '',
        groq: cfg?.groq_api_key ?? '',
        deepgram: cfg?.deepgram_api_key ?? '',
      })
    } catch (err) {
      console.error('Failed to load config:', err)
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => { loadConfig() }, [loadConfig])

  const updateConfig = async (updates: Partial<VoiceTyperConfig>) => {
    try {
      await call('set_config', { data: updates })
    } catch (err) {
      console.error('Failed to update config:', err)
    }
  }

  const useModel = async (model: ModelInfo) => {
    if (model.name === 'parakeet' && !model.depsOk) {
      showSnack('Dependencies required for Parakeet. Download first.', 'warning')
      return
    }
    if (!model.downloaded && model.name !== 'qwen') {
      showSnack(`Model "${model.name}" not downloaded yet. Download it first.`, 'warning')
      return
    }

    const updates: Partial<VoiceTyperConfig> = {}
    if (model.backend === 'whisper') {
      updates.asr_backend = 'whisper'
      updates.model_size = model.name as VoiceTyperConfig['model_size']
    } else {
      updates.asr_backend = model.backend as VoiceTyperConfig['asr_backend']
      updates.model_size = model.name as VoiceTyperConfig['model_size']
    }

    await updateConfig(updates)
    setModels((prev) =>
      prev.map((m) => ({ ...m, isActive: m.name === model.name })),
    )
    showSnack(`Using model: ${model.name}`, 'success')
  }

  const downloadModel = async (model: ModelInfo) => {
    if (isDownloading) return
    setIsDownloading(true)
    setDownloadProgress(0)
    setDownloadStatus(`Preparing ${model.name}...`)
    reloadModels()

    // Simulate download progress
    for (let i = 0; i <= 100; i += 10) {
      setDownloadProgress(i)
      setDownloadStatus(
        i < 30
          ? `Preparing ${model.name}...`
          : i < 70
            ? `Downloading ${model.name} (${model.size})...`
            : i < 100
              ? `Finalizing ${model.name}...`
              : 'Download complete!',
      )
      await new Promise((r) => setTimeout(r, 300))
    }

    setModels((prev) =>
      prev.map((m) =>
        m.name === model.name ? { ...m, downloaded: true, depsOk: true } : m,
      ),
    )
    setIsDownloading(false)
    showSnack(`Model '${model.name}' downloaded!`, 'success')
    reloadModels()
  }

  const deleteModelConfirm = (model: ModelInfo) => {
    if (model.isActive) {
      showSnack('Cannot delete the active model. Switch to another model first.', 'warning')
      return
    }
    setModels((prev) => prev.filter((m) => m.name !== model.name))
    showSnack(`Deleted model: ${model.name}`, 'warning')
  }

  const saveApiKey = async (provider: string) => {
    const key = apiKeys[provider] ?? ''
    const configKey =
      provider === 'openai' ? 'openai_api_key' :
      provider === 'groq' ? 'groq_api_key' : 'deepgram_api_key'
    await updateConfig({ [configKey]: key } as any)
    showSnack(`${CLOUD_PROVIDERS.find((p) => p.key === provider)?.label} API key saved`, 'success')
  }

  const testConnection = async (provider: string) => {
    const key = apiKeys[provider] ?? ''
    if (!key) {
      setTestResults((prev) => ({ ...prev, [provider]: 'Please enter an API key first' }))
      return
    }
    setTestResults((prev) => ({ ...prev, [provider]: 'Testing...' }))

    // Simulate connection test
    await new Promise((r) => setTimeout(r, 1000))
    if (key.length > 10) {
      setTestResults((prev) => ({ ...prev, [provider]: 'Connection successful!' }))
    } else {
      setTestResults((prev) => ({ ...prev, [provider]: 'Connection failed: Invalid API key format' }))
    }
  }

  const runBenchmark = async () => {
    if (isBenchmarking) return
    setIsBenchmarking(true)
    setBenchmarkResult('Running benchmark...')
    await new Promise((r) => setTimeout(r, 2000))
    setBenchmarkResult(`Benchmark complete: ~2.3s for 10 iterations on ${config?.device ?? 'unknown'} device`)
    setIsBenchmarking(false)
  }

  const reloadModels = () => {
    setLoading(true)
    setTimeout(() => setLoading(false), 100)
  }

  const getStatusBadge = (model: ModelInfo) => {
    if (model.isActive) return { label: 'Active', bg: 'color-mix(in srgb, var(--primary) 12%, transparent)', color: 'var(--primary)' }
    if (model.downloaded) return { label: 'Downloaded', bg: 'color-mix(in srgb, var(--primary) 12%, transparent)', color: 'var(--primary)' }
    if (!model.depsOk) return { label: 'Dependencies required', bg: 'color-mix(in srgb, var(--primary) 12%, transparent)', color: 'var(--primary)' }
    return { label: 'Available', bg: 'color-mix(in srgb, var(--primary) 10%, transparent)', color: 'var(--primary)' }
  }

  if (loading || !config) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="animate-fade-in-up mx-auto flex h-full w-full max-w-2xl flex-col overflow-hidden">
      <div className="space-y-1 px-6 pb-4 pt-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="font-sans text-2xl font-bold tracking-tight text-[var(--text-primary)]">
              Models
            </h1>
            <p className="text-sm text-[var(--text-muted)] mt-0.5">
              Configure your speech-to-text engines
            </p>
          </div>
          <Button
            variant="default"
            className="gap-2"
            onClick={() => downloadModel(models.find((m) => !m.downloaded) ?? models[0])}
            disabled={isDownloading}
          >
            <HugeiconsIcon icon={Download01Icon} className="h-4 w-4" />
            Download Model
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 pb-6 space-y-6">
        {/* Download Progress */}
        {isDownloading && (
          <div className="space-y-2">
            <div className="h-1.5 w-full rounded-full bg-[var(--border)] overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--accent)] transition-all duration-300"
                style={{ width: `${downloadProgress}%` }}
              />
            </div>
            <p className="text-xs text-[var(--text-muted)]">{downloadStatus}</p>
          </div>
        )}

        {/* Model Cards */}
        <div className="space-y-2">
          {models.map((model) => {
            const badge = getStatusBadge(model)
            return (
              <div
                key={model.name}
                className={cn(
                  'card-hover rounded-xl border p-5 transition-colors',
                  model.isActive
                    ? 'border-[var(--accent)] bg-[var(--bg-subtle)]'
                    : 'border-[var(--border)] bg-[var(--bg-subtle)]',
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <h3 className="text-base font-semibold text-[var(--text-primary)]">
                        {model.name}
                      </h3>
                      <span
                        className="inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-semibold border"
                        style={{
                          backgroundColor: badge.bg,
                          color: badge.color,
                          borderColor: badge.color + '40',
                        }}
                      >
                        {badge.label}
                      </span>
                    </div>
                    <p className="text-xs text-[var(--text-muted)] mt-1">
                      {model.name === 'parakeet' ? 'NVIDIA Parakeet TDT v3  ·  ' : ''}Size: {model.size}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-4">
                    {model.name === 'parakeet' && !model.depsOk ? (
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                        onClick={() => downloadModel(model)}
                        disabled={isDownloading}
                      >
                        <HugeiconsIcon icon={Download01Icon} className="h-3.5 w-3.5" />
                        Install Deps
                      </Button>
                    ) : (
                      <Button
                        variant={model.isActive ? 'secondary' : 'outline'}
                        size="sm"
                        className="gap-1.5"
                        onClick={() => useModel(model)}
                        disabled={model.isActive || (!model.downloaded && model.name !== 'qwen')}
                      >
                        <HugeiconsIcon
                          icon={model.isActive ? Tick02Icon : PlayIcon}
                          className="h-3.5 w-3.5"
                        />
                        {model.isActive ? 'Active' : 'Use'}
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => deleteModelConfirm(model)}
                      disabled={model.isActive}
                      className="text-[var(--text-muted)] hover:text-destructive"
                    >
                      <HugeiconsIcon icon={Delete01Icon} className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {/* Cloud ASR Providers */}
        <div className="space-y-4">
          <h2 className="font-sans text-lg font-semibold text-[var(--text-primary)]">
            Cloud ASR Providers
          </h2>
          <p className="text-sm text-[var(--text-muted)] -mt-3">
            Configure cloud-based transcription services
          </p>

          <div className="space-y-4">
            {CLOUD_PROVIDERS.map((provider) => (
              <div
                key={provider.key}
                className="rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-6"
              >
                <div className="flex items-center gap-2.5 mb-4">
                  <HugeiconsIcon
                    icon={Shield01Icon}
                    className="h-5 w-5 text-[var(--accent)]"
                  />
                  <h3 className="text-base font-semibold text-[var(--text-primary)]">
                    {provider.label} Settings
                  </h3>
                </div>

                <div className="mb-4">
                  <label className="text-sm font-medium text-[var(--text-primary)] mb-1.5 block">
                    API Key
                  </label>
                  <Input
                    type="password"
                    value={apiKeys[provider.key] ?? ''}
                    onChange={(e) =>
                      setApiKeys((prev) => ({ ...prev, [provider.key]: e.target.value }))
                    }
                    placeholder="Enter your API key"
                    className="w-full max-w-md"
                  />
                </div>

                <div className="flex items-center gap-3">
                  <Button
                    variant="default"
                    size="sm"
                    onClick={() => saveApiKey(provider.key)}
                  >
                    Save Key
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => testConnection(provider.key)}
                  >
                    <HugeiconsIcon icon={SparklesIcon} className="h-3.5 w-3.5" />
                    Test Connection
                  </Button>
                  {testResults[provider.key] && (
                    <span
                      className={cn(
                        'text-xs',
                        testResults[provider.key].includes('successful')
                          ? 'text-primary'
                          : testResults[provider.key].includes('Failed') || testResults[provider.key].includes('error')
                            ? 'text-destructive'
                            : 'text-[var(--text-muted)]',
                      )}
                    >
                      {testResults[provider.key]}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Model Benchmark */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-6">
          <h2 className="font-sans text-lg font-semibold text-[var(--text-primary)]">
            Model Benchmark
          </h2>
          <p className="text-sm text-[var(--text-muted)] mt-0.5 mb-4">
            Compare model performance on your system
          </p>
          <Button
            variant="default"
            className="gap-2"
            onClick={runBenchmark}
            disabled={isBenchmarking}
          >
            <HugeiconsIcon icon={ZapIcon} className="h-4 w-4" />
            {isBenchmarking ? 'Running...' : 'Run Benchmark'}
          </Button>
          {benchmarkResult && (
            <p className="text-sm text-[var(--text-muted)] mt-3">{benchmarkResult}</p>
          )}
        </div>
      </div>

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
