import { useState, useEffect, useCallback, useRef } from 'react'
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
import { NumberInput } from '@/components/ui/number-input'
import PageHeading from '@/components/PageHeading'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { HugeiconsIcon } from '@hugeicons/react'
import { File02Icon, RefreshIcon } from '@hugeicons/core-free-icons'
import type { VoiceTyperConfig } from '@/types/config'

// Module-level cache — persists across page navigations so settings render
// instantly on re-visit instead of showing a loading spinner.
let _cachedConfig: VoiceTyperConfig | null = null

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

const BUBBLE_POSITION_OPTIONS = [
  { value: 'top', label: 'Top Center' },
  { value: 'bottom', label: 'Bottom Center' },
] as const

const BUBBLE_BEHAVIOR_OPTIONS = [
  { value: 'show_on_record', label: 'Show on Record' },
  { value: 'always_visible', label: 'Always Visible' },
] as const

const LLM_PRESET_OPTIONS = [
  { value: 'professional', label: 'Professional' },
  { value: 'casual', label: 'Casual' },
  { value: 'email', label: 'Email' },
  { value: 'code', label: 'Code' },
] as const

interface SettingsPageProps {
  onThemeChange?: (mode: VoiceTyperConfig['theme_mode']) => void
}

export default function SettingsPage({ onThemeChange }: SettingsPageProps) {
  const { call } = usePython()
  const [config, setConfig] = useState<VoiceTyperConfig | null>(_cachedConfig)
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

  useEffect(() => {
    loadConfig()
  }, [loadConfig])

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

  // UX-007: debounced update for text inputs that fire on every keystroke.
  // Keeps a local draft in component state; commits via updateConfig after
  // 500ms of idle.  Prevents 11 IPC roundtrips when typing "gpt-4o-mini".
  const debouncedTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  const updateConfigDebounced = useCallback(
    (key: keyof VoiceTyperConfig, value: unknown, delayMs = 500) => {
      // Update local state immediately for responsive UI
      if (config) {
        const newConfig = { ...config, [key]: value }
        _cachedConfig = newConfig
        setConfig(newConfig)
      }
      // Clear any pending timer for this key
      if (debouncedTimers.current[key as string]) {
        clearTimeout(debouncedTimers.current[key as string])
      }
      // Schedule the IPC commit
      debouncedTimers.current[key as string] = setTimeout(() => {
        updateConfig({ [key]: value } as Partial<VoiceTyperConfig>)
        delete debouncedTimers.current[key as string]
      }, delayMs)
    },
    [config, updateConfig],
  )

  // Cleanup pending timers on unmount
  useEffect(() => {
    return () => {
      Object.values(debouncedTimers.current).forEach(clearTimeout)
    }
  }, [])

  const viewLogs = async () => {
    // UX-008: actually open the log folder via the main process.
    // Previously this just showed a snackbar without opening anything.
    try {
      const result = await (window as any).window_.openLogs()
      if (result?.success) {
        showSnack('Log folder opened', 'success')
      } else {
        showSnack(result?.error || 'Could not open log folder', 'error')
      }
    } catch (err) {
      console.error('Failed to open logs:', err)
      showSnack('Could not open log folder', 'error')
    }
  }

  const resetToDefaults = async () => {
    if (!config) return
    setShowResetDialog(false)
    // UX-018: fetch defaults from the Python backend instead of
    // hardcoding 22+ field values here (which silently drift from
    // the Config dataclass).  The backend returns a sanitized dict
    // (API keys redacted) which we send back via set_config.
    try {
      const defaults = await call('get_defaults')
      if (defaults && typeof defaults === 'object') {
        // Filter out the redacted sentinels and any non-allowlisted
        // keys before sending back via set_config.
        const safeDefaults: Record<string, unknown> = {}
        for (const [key, value] of Object.entries(defaults as Record<string, unknown>)) {
          // Skip redacted API keys — we don't want to overwrite the
          // user's real keys with "<redacted>".
          if (value === '<redacted>') continue
          // Skip schema_version and internal state fields.
          if (['schema_version', 'wayland_warned', 'onboarding_completed'].includes(key)) continue
          safeDefaults[key] = value
        }
        await updateConfig(safeDefaults as Partial<VoiceTyperConfig>)
        showSnack('Settings reset to defaults', 'success')
      } else {
        showSnack('Failed to fetch defaults from backend', 'error')
      }
    } catch (err) {
      console.error('Failed to reset to defaults:', err)
      showSnack('Failed to reset to defaults', 'error')
    }
  }

  const handleThemeChange = (mode: string) => {
    const m = mode as VoiceTyperConfig['theme_mode']
    // Keep local state in sync so the Select doesn't revert and updateConfig doesn't overwrite
    setConfig(prev => prev ? { ...prev, theme_mode: m } : prev)
    if (_cachedConfig) _cachedConfig = { ..._cachedConfig, theme_mode: m }
    // Theme is saved and applied by the App-level handler (which updates state + saves)
    onThemeChange?.(m)
  }

  if (!config) {
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
    <div className="min-h-full">
      <div className="mx-auto max-w-2xl space-y-8 px-6 pt-28 pb-6">
        {/* Header */}
        <PageHeading
          title="Settings"
          description="Adjust Voice Typer to your preferences."
        />

        {/* ── SECTION: General ──────────────────────────────────── */}
        <SettingsSection
          title="General"
          description="Behavior, startup, and appearance."
        >
          <SettingRow label="Launch at Login" info="Automatically start Voice Typer when you log into Windows.">
            <Switch
              checked={config.autostart}
              onCheckedChange={(checked) => updateConfig({ autostart: checked })}
            />
          </SettingRow>

          <SettingRow
            label="Fast Startup"
            info="Keep the speech model cached between restarts so the app is ready faster. Recommended."
          >
            <Switch
              checked={config.fast_startup ?? true}
              onCheckedChange={(checked) => updateConfig({ fast_startup: checked })}
            />
          </SettingRow>

          <SettingRow label="Notifications" info="Show a desktop notification when transcription completes or an error occurs.">
            <Switch
              checked={config.show_notifications}
              onCheckedChange={(checked) => updateConfig({ show_notifications: checked })}
            />
          </SettingRow>

          <SettingRow label="Theme" info="Choose between light, dark, or follow your system setting. Use the theme picker in the sidebar for quick access.">
            <span className="text-sm text-(--text-muted)">
              {config.theme_mode === 'system' ? 'System' : config.theme_mode === 'dark' ? 'Dark' : 'Light'}
              {' (change in sidebar)'}
            </span>
          </SettingRow>

          <SettingRow label="Tray Click" info="What happens when you left-click the Voice Typer icon in the system tray.">
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
        </SettingsSection>

        {/* ── SECTION: Overlay ──────────────────────────────────── */}
        <SettingsSection
          title="Overlay"
          description="Floating recording bubble."
        >
          {/* ── Dropdowns ──────────────────────────────────────── */}
          <SettingRow label="Bubble Behavior" info="Show the bubble only while recording, or keep it visible at all times.">
            <Select
              value={config.bubble_behavior ?? 'show_on_record'}
              onValueChange={(v) => {
                updateConfig({ bubble_behavior: v as 'show_on_record' | 'always_visible' })
              }}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BUBBLE_BEHAVIOR_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Bubble Position" info="Where the bubble appears on screen — top or bottom center.">
            <Select
              value={config.bubble_position ?? 'top'}
              onValueChange={(v) => {
                updateConfig({ bubble_position: v as 'top' | 'bottom' })
                // Notify the main process immediately so the bubble repositions.
                ;(window as any).bubble?.setPosition?.(v)
              }}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BUBBLE_POSITION_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>

          {/* ── Switches ───────────────────────────────────────── */}
          {/* Show on app startup toggle — only visible when Always Visible is selected */}
          {config.bubble_behavior === 'always_visible' && (
            <SettingRow
              label="Show on App Startup"
              info="Show the bubble as soon as the app opens. When off, it appears only when you start recording."
            >
              <Switch
                checked={config.bubble_show_on_startup ?? true}
                onCheckedChange={(checked) => updateConfig({ bubble_show_on_startup: checked })}
              />
            </SettingRow>
          )}

          <SettingRow label="Drag to Move" info="Allow dragging the bubble with your mouse to reposition it on screen.">
            <Switch
              checked={config.bubble_draggable ?? true}
              onCheckedChange={(checked) => {
                updateConfig({ bubble_draggable: checked })
                // Notify the main process immediately so the bubble responds.
                ;(window as any).bubble?.setDraggable?.(checked)
              }}
            />
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Hotkey ───────────────────────────────────── */}
        <SettingsSection
          title="Hotkey"
          description="Key to start and stop dictation."
        >
          <SettingRow label="Dictation Key" info="The keyboard key used to start and stop recording.">
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
        </SettingsSection>

        {/* ── SECTION: Recording ─────────────────────────────────── */}
        <SettingsSection
          title="Recording"
          description="Behavior, shortcuts, and silence handling."
        >
          {/* ── Dropdowns ──────────────────────────────────────── */}
          <SettingRow label="Recording Mode" info="Toggle: press the key once to start and again to stop. Push-to-talk: hold the key while speaking.">
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

          <SettingRow label="Auto-Stop" info="Automatically stop recording after this many seconds of silence.">
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

          {/* ── Switches ───────────────────────────────────────── */}
          <SettingRow label="ESC to Cancel" info="Press Escape to cancel an active recording.">
            <Switch
              checked={config.esc_cancel_enabled ?? false}
              onCheckedChange={(checked) => updateConfig({ esc_cancel_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Auto-Paste" info="Automatically paste transcribed text into the currently focused field.">
            <Switch
              checked={config.paste_on_stop}
              onCheckedChange={(checked) => updateConfig({ paste_on_stop: checked })}
            />
          </SettingRow>

          {/* ── Inputs ─────────────────────────────────────────── */}
          <SettingRow label="Re-Paste Key" info="Keyboard shortcut to re-paste the last transcription.">
            <Input
              value={config.repaste_hotkey ?? '<ctrl>+<alt>+v'}
              onChange={(e) => updateConfigDebounced('repaste_hotkey', e.target.value)}
              className="w-32 font-mono text-center"
            />
          </SettingRow>

          <SettingRow label="Silence Warning" info="Seconds of silence before showing a warning to help catch microphone issues.">
            <div className="flex items-center gap-2">
              <NumberInput
                min={3}
                max={30}
                step={1}
                value={String(config.silence_warning_seconds)}
                onChange={(e) => updateConfigDebounced('silence_warning_seconds', Number(e.target.value))}
                className="w-20 text-center"
              />
              <span className="text-sm text-(--text-muted)">sec</span>
            </div>
          </SettingRow>

          <SettingRow label="Max Duration" info="Maximum recording length. Set to 0 for automatic (varies by device).">
            <div className="flex items-center gap-2">
              <NumberInput
                min={0}
                max={7200}
                step={1}
                value={String(config.max_recording_seconds)}
                onChange={(e) => updateConfigDebounced('max_recording_seconds', Number(e.target.value))}
                className="w-20 text-center"
              />
              <span className="text-sm text-(--text-muted)">sec</span>
            </div>
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Post-Processing ──────────────────────────── */}
        <SettingsSection
          title="Post-Processing"
          description="Cleanup, corrections, and language."
        >
          <SettingRow label="Language" info="Auto-detect the spoken language, or pick one for better accuracy.">
            <Select
              value={config.language || 'auto'}
              onValueChange={(v) => updateConfig({ language: v === 'auto' ? '' : v })}
            >
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

          <SettingRow label="Auto Punctuation" info="Add periods, commas, and question marks automatically.">
            <Switch
              checked={config.auto_punctuation ?? false}
              onCheckedChange={(checked) => updateConfig({ auto_punctuation: checked })}
            />
          </SettingRow>

          <SettingRow label="Text Cleanup" info="Fix common misspellings, remove repeated words, and capitalize sentences.">
            <Switch
              checked={config.text_cleanup_enabled}
              onCheckedChange={(checked) => updateConfig({ text_cleanup_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Text Snippets" info="Use voice commands to insert pre-written text snippets with placeholders.">
            <Switch
              checked={config.templates_enabled ?? true}
              onCheckedChange={(checked) => updateConfig({ templates_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Vocabulary" info="Custom word replacements so the transcription uses your preferred terms.">
            <Switch
              checked={config.vocabulary_enabled ?? true}
              onCheckedChange={(checked) => updateConfig({ vocabulary_enabled: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: LLM Polishing ────────────────────────────── */}
        <SettingsSection
          title="LLM Polishing"
          description="AI-powered transcription enhancement."
        >
          <SettingRow label="Enable" info="Use an AI language model to clean up and improve the transcribed text. Requires an API key.">
            <Switch
              checked={config.llm_polish ?? false}
              onCheckedChange={(checked) => updateConfig({ llm_polish: checked })}
            />
          </SettingRow>

          {config.llm_polish && (
            <div className="animate-fade-in space-y-0 divide-y divide-border">
              <SettingRow label="API Key" info="Your OpenAI-compatible API key for the polishing service.">
                <div className="relative">
                  <Input
                    type={llmKeyVisible ? 'text' : 'password'}
                    /* SEC-003: backend redacts the key to '<redacted>' in
                     * get_config responses.  Show empty in that case so
                     * the user isn't tempted to "save" the sentinel back.
                     * When the user types a real key, updateConfig sends
                     * it via set_config (which is allowlisted). */
                    value={config.llm_api_key && config.llm_api_key !== '<redacted>' ? config.llm_api_key : ''}
                    onChange={(e) => updateConfigDebounced('llm_api_key', e.target.value)}
                    placeholder={config.llm_api_key === '<redacted>' ? '•••••••• (configured)' : ''}
                    className="w-56 pr-8"
                  />
                  <Button
                    variant="ghost"
                    size="xs"
                    onClick={() => setLlmKeyVisible(!llmKeyVisible)}
                    className="absolute right-1 top-1/2 -translate-y-1/2 text-xs"
                  >
                    {llmKeyVisible ? 'Hide' : 'Show'}
                  </Button>
                </div>
              </SettingRow>

              <SettingRow label="API URL" info="The endpoint URL for the AI language model service.">
                <Input
                  value={config.llm_api_url ?? 'https://api.openai.com/v1/chat/completions'}
                  onChange={(e) => updateConfigDebounced('llm_api_url', e.target.value)}
                  className="w-64"
                />
              </SettingRow>

              <SettingRow label="Model" info="The AI model to use for polishing (e.g., gpt-4o-mini).">
                <Input
                  value={config.llm_model ?? 'gpt-4o-mini'}
                  onChange={(e) => updateConfigDebounced('llm_model', e.target.value)}
                  className="w-44"
                />
              </SettingRow>

              <SettingRow label="Preset" info="The writing style to apply — professional, casual, email, or code.">
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
            </div>
          )}
        </SettingsSection>

        {/* ── SECTION: Audio & Recovery ─────────────────────────── */}
        <SettingsSection
          title="Audio & Recovery"
          description="Quality monitoring and safety."
        >
          <SettingRow label="Crash Recovery" info="Save recent transcriptions so they can be recovered if the app crashes before you paste them.">
            <Switch
              checked={config.crash_recovery_enabled ?? true}
              onCheckedChange={(checked) => updateConfig({ crash_recovery_enabled: checked })}
            />
          </SettingRow>

          <SettingRow label="Audio Warnings" info="Alert you about microphone issues like clipping, low volume, or background noise.">
            <Switch
              checked={config.audio_quality_warnings ?? true}
              onCheckedChange={(checked) => updateConfig({ audio_quality_warnings: checked })}
            />
          </SettingRow>
        </SettingsSection>

        {/* ── SECTION: Troubleshooting ──────────────────────────── */}
        <SettingsSection
          title="Troubleshooting"
          description="Diagnostic tools and support."
        >
          <div className="px-3.5 py-3.5 flex flex-wrap gap-3">
            <Button
              variant="outline"
              className="gap-2"
              onClick={viewLogs}
            >
              <HugeiconsIcon icon={File02Icon} strokeWidth={1.625} className="h-4 w-4" />
              View Logs
            </Button>
            <Button
              variant="destructive"
              className="gap-2"
              onClick={() => setShowResetDialog(true)}
            >
              <HugeiconsIcon icon={RefreshIcon} strokeWidth={1.625} className="h-4 w-4" />
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
        <div
          className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowResetDialog(false)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="reset-dialog-title"
        >
          <div
            className={cn(
              'animate-scale-in w-100 rounded-xl border border-border',
              'bg-(--bg) p-6 shadow-2xl',
            )}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setShowResetDialog(false)
            }}
          >
            <h2 id="reset-dialog-title" className="text-lg font-semibold text-(--text-primary) mb-3">
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
              <Button variant="destructive" onClick={resetToDefaults} autoFocus>
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
