// src/renderer/src/pages/Settings.tsx

import { useState, useEffect, useCallback } from 'react'
import { usePython } from '@/hooks/usePython'
import { SettingsSection } from '@/components/SettingsSection'
import { SettingRow } from '@/components/SettingRow'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import type { VoiceTyperConfig, MicrophoneDevice } from '@/types/config'

const MODEL_OPTIONS = [
  { value: 'tiny.en', label: 'Tiny', description: 'Fastest, lower accuracy' },
  { value: 'small.en', label: 'Small', description: 'Best balance (default)' },
  { value: 'medium.en', label: 'Medium', description: 'Higher accuracy, slower' },
  { value: 'qwen', label: 'Qwen ASR', description: 'Experimental, separate install' },
] as const

const LANGUAGE_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
  { value: 'ru', label: 'Russian' },
]

const SILENCE_WARNING_OPTIONS = [
  { value: 5, label: '5 seconds' },
  { value: 10, label: '10 seconds' },
  { value: 15, label: '15 seconds' },
  { value: 20, label: '20 seconds' },
]

const AUTO_STOP_OPTIONS = [
  { value: 60, label: '1 minute' },
  { value: 120, label: '2 minutes' },
  { value: 180, label: '3 minutes' },
  { value: 300, label: '5 minutes' },
]

const MAX_RECORDING_OPTIONS = [
  { value: 300, label: '5 minutes' },
  { value: 600, label: '10 minutes' },
  { value: 900, label: '15 minutes' },
  { value: 1200, label: '20 minutes' },
]

export default function SettingsPage() {
  const { call } = usePython()
  const [config, setConfig] = useState<VoiceTyperConfig | null>(null)
  const [microphones, setMicrophones] = useState<MicrophoneDevice[]>([])
  const [saving, setSaving] = useState(false)

  const loadConfig = useCallback(async () => {
    try {
      const result = await call<VoiceTyperConfig>('get_config')
      setConfig(result)
    } catch (err) {
      console.error('Failed to load config:', err)
    }
  }, [call])

  const loadMicrophones = useCallback(async () => {
    try {
      const result = await call<MicrophoneDevice[]>('get_microphones')
      setMicrophones(result)
    } catch (err) {
      console.error('Failed to load microphones:', err)
    }
  }, [call])

  useEffect(() => {
    loadConfig()
    loadMicrophones()
  }, [loadConfig, loadMicrophones])

  const updateConfig = useCallback(
    async (updates: Partial<VoiceTyperConfig>) => {
      if (!config) return
      setSaving(true)
      try {
        const newConfig = { ...config, ...updates }
        setConfig(newConfig)
        await call('update_config', { data: updates })
      } catch (err) {
        console.error('Failed to update config:', err)
        await loadConfig()
      } finally {
        setSaving(false)
      }
    },
    [config, call, loadConfig],
  )

  if (!config) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="space-y-2 text-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent mx-auto" />
          <p className="text-sm text-[var(--text-muted)]">Loading settings...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-2xl space-y-8 px-6 py-8">
        {/* Header */}
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            Settings
          </h1>
          <p className="text-sm text-[var(--text-muted)]">
            Configure Voice Typer preferences. Changes apply immediately.
          </p>
        </div>

        {/* Input Section */}
        <SettingsSection
          title="Input"
          description="Configure how Voice Typer captures and processes your speech."
        >
          <SettingRow label="Hotkey" description="Press this key to toggle dictation on and off.">
            <Select
              value={config.hotkey.replace(/[<>]/g, '')}
              onValueChange={(v) => updateConfig({ hotkey: `<${v}>` })}
            >
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {['f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12'].map(
                  (key) => (
                    <SelectItem key={key} value={key}>
                      {key.toUpperCase()}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Microphone" description="Select the audio input device for recording.">
            <Select
              value={config.microphone ?? 'default'}
              onValueChange={(v) =>
                updateConfig({ microphone: v === 'default' ? null : v })
              }
            >
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="default">System Default</SelectItem>
                {microphones.map((mic) => (
                  <SelectItem key={mic.index} value={String(mic.index)}>
                    {mic.name}
                    <span className="ml-2 text-xs text-[var(--text-muted)]">({mic.host_api})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Language" description="Primary language for transcription accuracy.">
            <Select value={config.language} onValueChange={(v) => updateConfig({ language: v })}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LANGUAGE_OPTIONS.map((lang) => (
                  <SelectItem key={lang.value} value={lang.value}>
                    {lang.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>
        </SettingsSection>

        {/* Model Section */}
        <SettingsSection
          title="Transcription Model"
          description="Larger models are more accurate but slower and use more memory."
        >
          <SettingRow label="Model" description="Whisper model size for speech recognition.">
            <Select
              value={config.model_size}
              onValueChange={(v) =>
                updateConfig({ model_size: v as VoiceTyperConfig['model_size'] })
              }
            >
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODEL_OPTIONS.map((model) => (
                  <SelectItem key={model.value} value={model.value}>
                    <span>{model.label}</span>
                    <span className="ml-2 text-xs text-[var(--text-muted)]">
                      {model.description}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow
            label="GPU Acceleration"
            description="Use CUDA for faster transcription. Falls back to CPU automatically."
          >
            <Select
              value={config.device}
              onValueChange={(v) =>
                updateConfig({ device: v as VoiceTyperConfig['device'] })
              }
            >
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="cuda">GPU (CUDA)</SelectItem>
                <SelectItem value="cpu">CPU</SelectItem>
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow
            label="Streaming Transcription"
            description="Transcribe during recording for faster results. Recommended."
          >
            <Switch
              checked={config.streaming_transcription}
              onCheckedChange={(checked) => updateConfig({ streaming_transcription: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* Output Section */}
        <SettingsSection
          title="Output"
          description="Control how transcribed text is delivered to your applications."
        >
          <SettingRow
            label="Auto-Paste"
            description="Automatically paste text into the focused field after transcription."
          >
            <Switch
              checked={config.paste_on_stop}
              onCheckedChange={(checked) => updateConfig({ paste_on_stop: checked })}
            />
          </SettingRow>

          <SettingRow
            label="Text Cleanup"
            description="Fix misspellings, remove duplicates, and capitalize sentences."
          >
            <Switch
              checked={config.text_cleanup_enabled}
              onCheckedChange={(checked) => updateConfig({ text_cleanup_enabled: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* Safety Section */}
        <SettingsSection
          title="Safety"
          description="Prevent runaway recordings and get alerts for microphone issues."
        >
          <SettingRow
            label="Silence Warning"
            description="Notify when the microphone has been silent for this duration."
          >
            <Select
              value={String(config.silence_warning_seconds)}
              onValueChange={(v) => updateConfig({ silence_warning_seconds: Number(v) })}
            >
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SILENCE_WARNING_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={String(opt.value)}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow
            label="Auto-Stop Timeout"
            description="Stop recording automatically after this period of silence."
          >
            <Select
              value={String(config.silence_auto_stop_seconds)}
              onValueChange={(v) => updateConfig({ silence_auto_stop_seconds: Number(v) })}
            >
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AUTO_STOP_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={String(opt.value)}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow
            label="Max Recording Duration"
            description="Hard limit on recording length. Prevents excessive memory usage."
          >
            <Select
              value={String(config.max_recording_seconds)}
              onValueChange={(v) => updateConfig({ max_recording_seconds: Number(v) })}
            >
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">Default</SelectItem>
                {MAX_RECORDING_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={String(opt.value)}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>
        </SettingsSection>

        {/* System Section */}
        <SettingsSection
          title="System"
          description="Application behavior and startup preferences."
        >
          <SettingRow
            label="Start on Login"
            description="Launch Voice Typer automatically when you sign in to Windows."
          >
            <Switch
              checked={config.autostart}
              onCheckedChange={(checked) => updateConfig({ autostart: checked })}
            />
          </SettingRow>

          <SettingRow
            label="Desktop Notifications"
            description="Show notifications for transcription events and errors."
          >
            <Switch
              checked={config.show_notifications}
              onCheckedChange={(checked) => updateConfig({ show_notifications: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* Status indicator */}
        {saving && (
          <div className="fixed bottom-4 right-4 rounded-lg bg-[var(--surface)] border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-muted)] shadow-lg">
            Saving...
          </div>
        )}
      </div>
    </div>
  )
}
