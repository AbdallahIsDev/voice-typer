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
import PageHeading from '@/components/PageHeading'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { HugeiconsIcon } from '@hugeicons/react'
import { Mic02Icon, File02Icon, RefreshIcon } from '@hugeicons/core-free-icons'
import type { VoiceTyperConfig, MicrophoneDevice } from '@/types/config'

// Module-level cache — persists across page navigations so settings render
// instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null
let _cachedMicrophones: MicrophoneDevice[] = []

const MODEL_OPTIONS = [
  { value: 'tiny.en', label: 'Tiny', description: 'Fastest, lower accuracy' },
  { value: 'small.en', label: 'Small', description: 'Best balance (default)' },
  { value: 'medium.en', label: 'Medium', description: 'Higher accuracy, slower' },
  { value: 'qwen', label: 'Qwen ASR', description: 'Experimental, separate install' },
  { value: 'parakeet', label: 'Parakeet', description: 'NVIDIA TDT v3' },
] as const

const LANGUAGE_OPTIONS = [
  { value: 'auto', label: 'Auto-detect', description: 'Any language — no hallucination filtering' },
  { value: 'en', label: 'English', description: 'Enables Latin-script hallucination filter' },
  { value: 'zh', label: 'Chinese' },
  { value: 'es', label: 'Spanish' },
  { value: 'ar', label: 'Arabic' },
  { value: 'fr', label: 'French' },
  { value: 'ru', label: 'Russian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'de', label: 'German' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'it', label: 'Italian' },
  { value: 'nl', label: 'Dutch' },
  { value: 'pl', label: 'Polish' },
  { value: 'tr', label: 'Turkish' },
  { value: 'vi', label: 'Vietnamese' },
  { value: 'th', label: 'Thai' },
  { value: 'hi', label: 'Hindi' },
  { value: 'id', label: 'Indonesian' },
  { value: 'sv', label: 'Swedish' },
  { value: 'da', label: 'Danish' },
  { value: 'fi', label: 'Finnish' },
  { value: 'no', label: 'Norwegian' },
  { value: 'cs', label: 'Czech' },
  { value: 'ro', label: 'Romanian' },
  { value: 'hu', label: 'Hungarian' },
  { value: 'el', label: 'Greek' },
  { value: 'he', label: 'Hebrew' },
]

const AUTO_STOP_OPTIONS = [
  { value: 60, label: '1 minute' },
  { value: 120, label: '2 minutes' },
  { value: 180, label: '3 minutes' },
  { value: 300, label: '5 minutes' },
]



const RECORDING_MODE_OPTIONS = [
  { value: 'toggle', label: 'Toggle (F2)' },
  { value: 'push_to_talk', label: 'Push-to-Talk' },
] as const

const THEME_OPTIONS = [
  { value: 'system', label: 'System Default' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
] as const

const TRAY_CLICK_OPTIONS = [
  { value: 'open_app', label: 'Open App' },
  { value: 'toggle_dictation', label: 'Toggle Dictation' },
] as const

const LLM_PRESET_OPTIONS = [
  { value: 'professional', label: 'Professional' },
  { value: 'casual', label: 'Casual' },
  { value: 'email', label: 'Email' },
  { value: 'code', label: 'Code' },
] as const

export default function SettingsPage() {
  const { call } = usePython()
  const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig)
  const [microphones, setMicrophones] = useState<MicrophoneDevice[]>(_cachedMicrophones)
  const [saving, setSaving] = useState(false)
  const [showResetDialog, setShowResetDialog] = useState(false)
  const [snackbar, setSnackbar] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null)
  const [llmKeyVisible, setLlmKeyVisible] = useState(false)

  const showSnack = (message: string, type: 'success' | 'error' | 'warning') => {
    setSnackbar({ message, type })
    setTimeout(() => setSnackbar(null), 3000)
  }

  const loadConfig = useCallback(async () => {
    try {
      const result = await call<VoiceTyperConfig>('get_config')
      _cachedConfig = result
      setConfig(result)
    } catch (err) {
      console.error('Failed to load config:', err)
    }
  }, [call])

  const loadMicrophones = useCallback(async () => {
    try {
      const result = await call<MicrophoneDevice[]>('get_microphones')
      _cachedMicrophones = result
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
        _cachedConfig = newConfig
        setConfig(newConfig)
        await call('set_config', updates)
      } catch (err) {
        console.error('Failed to update config:', err)
        await loadConfig()
      } finally {
        setSaving(false)
      }
    },
    [config, call, loadConfig],
  )

  const testMicrophone = async () => {
    try {
      const mics = await call<MicrophoneDevice[]>('get_microphones')
      if (mics && mics.length > 0) {
        const names = mics.slice(0, 5).map((m) => m.name).join(', ')
        showSnack(`Found ${mics.length} mic(s): ${names}`, 'success')
      } else {
        showSnack('No microphones detected', 'warning')
      }
    } catch (err) {
      showSnack('Mic test failed', 'error')
    }
  }

  const viewLogs = () => {
    showSnack('Log folder opened', 'success')
  }

  const resetToDefaults = () => {
    if (!config) return
    setShowResetDialog(false)
    const defaults: Partial<VoiceTyperConfig> = {
      recording_mode: 'toggle',
      esc_cancel_enabled: false,
      auto_punctuation: false,
      templates_enabled: true,
      vocabulary_enabled: true,
      llm_polish: false,
      crash_recovery_enabled: true,
      audio_quality_warnings: true,
      audio_clipping_warning: true,
      audio_low_volume_warning: true,
      audio_noise_warning: true,
      paste_on_stop: true,
      text_cleanup_enabled: true,
      silence_warning_seconds: 20,
      max_recording_seconds: 0,
      autostart: true,
      show_notifications: true,
      fast_startup: true,
      tray_left_click_action: 'open_app',
      theme_mode: 'system',
      high_contrast: false,
      text_size: 14,
      streaming_transcription: true,
    }
    updateConfig(defaults)
    showSnack('Settings reset to defaults', 'success')
  }

  if (!_cachedConfig && !config) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="space-y-2 text-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent mx-auto" />
          <p className="text-sm text-(--text-muted)">Loading settings...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="animate-fade-in-up min-h-full">
      <div className="mx-auto max-w-2xl space-y-8 px-6 py-6">
        {/* Header */}
        <PageHeading
          title="Settings"
          description="Manage Voice Typer preferences and behavior."
        />

        {/* ── SECTION: Application ─────────────────────────────── */}
        <SettingsSection
          title="Application"
          description="Application behavior and startup preferences."
        >
          <SettingRow label="Start on Login" description="Launch Voice Typer when Windows starts.">
            <Switch
              checked={config.autostart}
              onCheckedChange={(checked) => updateConfig({ autostart: checked })}
            />
          </SettingRow>

          <SettingRow
            label="Fast Startup"
            description="Keep the speech model cached in memory between reboots so the app starts in seconds instead of ~45s. Runs a brief background task shortly after login. Recommended."
          >
            <Switch
              checked={config.fast_startup ?? true}
              onCheckedChange={(checked) => updateConfig({ fast_startup: checked })}
            />
          </SettingRow>

          <SettingRow label="Desktop Notifications" description="Show notifications for transcription events and errors.">
            <Switch
              checked={config.show_notifications}
              onCheckedChange={(checked) => updateConfig({ show_notifications: checked })}
            />
          </SettingRow>

          <SettingRow label="Tray Left-click Action" description="What happens when you left-click the tray icon.">
            <Select
              value={config.tray_left_click_action ?? 'open_app'}
              onValueChange={(v) => updateConfig({ tray_left_click_action: v as 'open_app' | 'toggle_dictation' })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRAY_CLICK_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Theme" description="Choose light, dark, or system theme.">
            <Select
              value={config.theme_mode ?? 'system'}
              onValueChange={(v) => updateConfig({ theme_mode: v as 'system' | 'light' | 'dark' })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {THEME_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Input ─────────────────────────────────── */}
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
                    <span className="ml-2 text-xs text-(--text-muted)">({mic.host_api})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Transcription Model ─────────────────────── */}
        <SettingsSection
          title="Transcription Model"
          description="Larger models are more accurate but slower and use more memory."
        >
          <SettingRow label="Model" description="Model size for speech recognition.">
            <Select
              value={config.model_size}
              onValueChange={(v) =>
                updateConfig({ model_size: v as VoiceTyperConfig['model_size'] })
              }
            >
              <SelectTrigger className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODEL_OPTIONS.map((model) => (
                  <SelectItem key={model.value} value={model.value}>
                    <span>{model.label}</span>
                    <span className="ml-2 text-xs text-(--text-muted)">
                      {model.description}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="GPU Acceleration" description="Use CUDA for faster transcription. Falls back to CPU automatically.">
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

          <SettingRow label="Streaming Transcription" description="Transcribe during recording for faster results. Recommended.">
            <Switch
              checked={config.streaming_transcription}
              onCheckedChange={(checked) => updateConfig({ streaming_transcription: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Recording ──────────────────────────────── */}
        <SettingsSection
          title="Recording"
          description="Configure recording behavior and shortcuts."
        >
          <SettingRow label="Recording Mode" description="Toggle: press to start/stop. Push-to-talk: hold to record.">
            <Select
              value={config.recording_mode ?? 'toggle'}
              onValueChange={(v) => updateConfig({ recording_mode: v as 'toggle' | 'push_to_talk' })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RECORDING_MODE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="ESC to Cancel" description="Press Escape to cancel current recording.">
            <Switch
              checked={config.esc_cancel_enabled ?? false}
              onCheckedChange={(checked) => updateConfig({ esc_cancel_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Repaste Hotkey" description="Hotkey for repasting last transcription.">
            <Input
              value={config.repaste_hotkey ?? '<ctrl>+<alt>+v'}
              onChange={(e) => updateConfig({ repaste_hotkey: e.target.value })}
              className="w-32 font-mono text-center"
            />
          </SettingRow>

          <SettingRow label="Auto-Paste" description="Paste text into the focused field after transcription.">
            <Switch
              checked={config.paste_on_stop}
              onCheckedChange={(checked) => updateConfig({ paste_on_stop: checked })}
            />
          </SettingRow>

          <SettingRow label="Snippets / Templates" description="Enable text snippets with variables.">
            <Switch
              checked={config.templates_enabled ?? true}
              onCheckedChange={(checked) => updateConfig({ templates_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Vocabulary Correction" description="Apply custom vocabulary corrections.">
            <Switch
              checked={config.vocabulary_enabled ?? true}
              onCheckedChange={(checked) => updateConfig({ vocabulary_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Text Cleanup" description="Fix misspellings, remove duplicates, and capitalize sentences.">
            <Switch
              checked={config.text_cleanup_enabled}
              onCheckedChange={(checked) => updateConfig({ text_cleanup_enabled: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Speech Processing ──────────────────────── */}
        <SettingsSection
          title="Speech Processing"
          description="Configure language, text processing, and AI-powered polishing."
        >
          <SettingRow
            label="Language"
            description="Auto-detect lets the model identify any language. Selecting a specific language improves accuracy and enables hallucination filtering for English."
          >
            <Select value={config.language || 'auto'} onValueChange={(v) => updateConfig({ language: v === 'auto' ? '' : v })}>
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LANGUAGE_OPTIONS.map((lang) => (
                  <SelectItem key={lang.value} value={lang.value}>
                    <span>{lang.label}</span>
                    {lang.description && (
                      <span className="ml-2 text-[10px] text-(--text-muted)">
                        {lang.description}
                      </span>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Auto-Punctuation" description="Add punctuation automatically after transcription.">
            <Switch
              checked={config.auto_punctuation ?? false}
              onCheckedChange={(checked) => updateConfig({ auto_punctuation: checked })}
            />
          </SettingRow>

          <SettingRow label="LLM Polishing" description="Use LLM to improve text quality (requires API key).">
            <Switch
              checked={config.llm_polish ?? false}
              onCheckedChange={(checked) => updateConfig({ llm_polish: checked })}
            />
          </SettingRow>

          <SettingRow label="LLM API Key" description="OpenAI-compatible API key for LLM polishing.">
            <div className="relative">
              <Input
                type={llmKeyVisible ? 'text' : 'password'}
                value={config.llm_api_key ?? ''}
                onChange={(e) => updateConfig({ llm_api_key: e.target.value })}
                className="w-56 pr-8"
              />
              <button
                onClick={() => setLlmKeyVisible(!llmKeyVisible)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-(--text-muted) hover:text-(--text-secondary) text-xs"
              >
                {llmKeyVisible ? 'Hide' : 'Show'}
              </button>
            </div>
          </SettingRow>

          <SettingRow label="LLM API URL" description="API endpoint URL for LLM service.">
            <Input
              value={config.llm_api_url ?? 'https://api.openai.com/v1/chat/completions'}
              onChange={(e) => updateConfig({ llm_api_url: e.target.value })}
              className="w-64"
            />
          </SettingRow>

          <SettingRow label="LLM Model" description="Model name (e.g., gpt-4o-mini).">
            <Input
              value={config.llm_model ?? 'gpt-4o-mini'}
              onChange={(e) => updateConfig({ llm_model: e.target.value })}
              className="w-44"
            />
          </SettingRow>

          <SettingRow label="LLM Preset" description="Polishing style preset.">
            <Select
              value={config.llm_preset ?? 'professional'}
              onValueChange={(v) => updateConfig({ llm_preset: v })}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LLM_PRESET_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Safety ────────────────────────────────── */}
        <SettingsSection
          title="Safety"
          description="Prevent runaway recordings and get alerts for microphone issues."
        >
          <SettingRow label="Silence Warning Timeout" description="Seconds before showing silence warning.">
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={3}
                max={30}
                step={1}
                value={String(config.silence_warning_seconds)}
                onChange={(e) => updateConfig({ silence_warning_seconds: Number(e.target.value) })}
                className="w-20 text-center"
              />
              <span className="text-sm text-(--text-muted)">sec</span>
            </div>
          </SettingRow>

          <SettingRow label="Auto-Stop Timeout" description="Stop recording after this period of silence.">
            <Select
              value={String(config.silence_auto_stop_seconds ?? 60)}
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

          <SettingRow label="Max Recording Timeout" description="Maximum recording duration in seconds (0 = auto).">
            <div className="flex items-center gap-2">
              <Input
                type="number"
                min={0}
                max={7200}
                step={1}
                value={String(config.max_recording_seconds)}
                onChange={(e) => updateConfig({ max_recording_seconds: Number(e.target.value) })}
                className="w-20 text-center"
              />
              <span className="text-sm text-(--text-muted)">sec</span>
            </div>
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Audio & Recovery ──────────────────────── */}
        <SettingsSection
          title="Audio & Recovery"
          description="Audio quality monitoring and crash recovery settings."
        >
          <SettingRow label="Crash Recovery" description="Save unpasted transcriptions for recovery after crash.">
            <Switch
              checked={config.crash_recovery_enabled ?? true}
              onCheckedChange={(checked) => updateConfig({ crash_recovery_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Audio Quality Warnings" description="Warn about clipping, low volume, or noise.">
            <Switch
              checked={config.audio_quality_warnings ?? true}
              onCheckedChange={(checked) => updateConfig({ audio_quality_warnings: checked })}
            />
          </SettingRow>

          <SettingRow label="Clipping Warning" description="Warn when audio is clipping (too loud).">
            <Switch
              checked={config.audio_clipping_warning ?? true}
              onCheckedChange={(checked) => updateConfig({ audio_clipping_warning: checked })}
            />
          </SettingRow>

          <SettingRow label="Low Volume Warning" description="Warn when audio is too quiet.">
            <Switch
              checked={config.audio_low_volume_warning ?? true}
              onCheckedChange={(checked) => updateConfig({ audio_low_volume_warning: checked })}
            />
          </SettingRow>

          <SettingRow label="Noise Warning" description="Warn when background noise is detected.">
            <Switch
              checked={config.audio_noise_warning ?? true}
              onCheckedChange={(checked) => updateConfig({ audio_noise_warning: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Accessibility ──────────────────────────── */}
        <SettingsSection
          title="Accessibility"
          description="Visual accessibility options."
        >
          <SettingRow label="High Contrast" description="Enable high-contrast mode for better visibility.">
            <Switch
              checked={config.high_contrast ?? false}
              onCheckedChange={(checked) => updateConfig({ high_contrast: checked })}
            />
          </SettingRow>

          <SettingRow label="Text Size" description="Adjust base text size (12-24px).">
            <div className="flex items-center gap-3 w-48">
              <input
                type="range"
                min={12}
                max={24}
                step={2}
                value={config.text_size ?? 14}
                onChange={(e) => updateConfig({ text_size: Number(e.target.value) })}
                className="flex-1 h-1.5 rounded-full bg-border appearance-none cursor-pointer
                  [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4
                  [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-accent
                  [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:cursor-pointer"
              />
              <span className="text-xs text-text-muted min-w-[2ch] text-center">
                {config.text_size ?? 14}
              </span>
            </div>
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Troubleshooting ────────────────────────── */}
        <SettingsSection
          title="Troubleshooting"
          description="Diagnostic tools and support."
        >
          <div className="px-3.5 py-3.5 flex flex-wrap gap-3">
            <Button
              variant="outline"
              className="gap-2"
              onClick={testMicrophone}
            >
              <HugeiconsIcon icon={Mic02Icon} className="h-4 w-4" />
              Test Microphone
            </Button>
            <Button
              variant="outline"
              className="gap-2"
              onClick={viewLogs}
            >
              <HugeiconsIcon icon={File02Icon} className="h-4 w-4" />
              View Logs
            </Button>
            <Button
              variant="destructive"
              className="gap-2"
              onClick={() => setShowResetDialog(true)}
            >
              <HugeiconsIcon icon={RefreshIcon} className="h-4 w-4" />
              Reset to Defaults
            </Button>
          </div>
        </SettingsSection>

        {/* Status indicator */}
        {saving && (
          <div className="fixed bottom-4 right-4 rounded-lg bg-(--surface) border border-border px-4 py-2 text-sm text-(--text-muted) shadow-lg">
            Saving...
          </div>
        )}
      </div>

      {/* Reset Confirmation Dialog */}
      {showResetDialog && (
        <div className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div
            className={cn(
              'animate-scale-in w-100 rounded-xl border border-border',
              'bg-(--bg) p-6 shadow-2xl',
            )}
          >
            <h2 className="text-lg font-semibold text-(--text-primary) mb-3">
              Reset to Defaults
            </h2>
            <p className="text-sm text-(--text-muted) mb-6">
              Are you sure you want to reset all settings to their default values?
              This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <Button variant="ghost" onClick={() => setShowResetDialog(false)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={resetToDefaults}>
                Reset to Defaults
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
