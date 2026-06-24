import { useState, useEffect, type ReactNode } from 'react'
import { usePython } from '@/hooks/usePython'
import { SettingsSection } from '@/components/SettingsSection'
import { Button } from '@/components/ui/button'
import type { VoiceTyperConfig } from '@/types/config'

// App version. The package.json is two directories above the
// renderer src tree, and the alias `@/../package.json` does not
// resolve cleanly under every TS config — fall back to a hardcoded
// constant matching package.json#version.
const APP_VERSION = '1.0.0'

const GITHUB_REPO = 'https://github.com/AbdallahIsDev/voice-typer'
const GITHUB_ISSUES = 'https://github.com/AbdallahIsDev/voice-typer/issues'
const SECURITY_URL =
  'https://github.com/AbdallahIsDev/voice-typer/blob/main/SECURITY.md'
const CONTRIBUTING_URL =
  'https://github.com/AbdallahIsDev/voice-typer/blob/main/CONTRIBUTING.md'

// Small label/value row that matches the visual rhythm of SettingRow
// but doesn't carry the input-association machinery (we're read-only).
function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 px-3.5 py-2.5">
      <span className="text-sm font-medium text-(--text-primary)">{label}</span>
      <span className="shrink-0 text-right text-sm text-(--text-muted)">
        {value}
      </span>
    </div>
  )
}

function StatusDot({ connected }: { connected: boolean }) {
  return (
    <span
      className={
        'inline-flex items-center gap-1.5 ' +
        (connected ? 'text-(--text-primary)' : 'text-destructive')
      }
    >
      <span
        className={
          'size-1.5 rounded-full ' +
          (connected ? 'bg-emerald-500' : 'bg-destructive')
        }
      />
      {connected ? 'Connected' : 'Disconnected'}
    </span>
  )
}

export default function AboutPage() {
  const { call } = usePython()
  const [config, setConfig] = useState<VoiceTyperConfig | null>(null)
  const [configDir, setConfigDir] = useState<string>('~/.voice-typer')
  // null = still probing, true/false = settled.
  const [backendConnected, setBackendConnected] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      // Probe backend connectivity by issuing get_status. If the
      // Python backend is down (or the bridge isn't installed), the
      // call rejects and we mark the backend as disconnected.
      try {
        const status = await call<{ config_dir?: string; status?: string }>(
          'get_status',
        )
        if (!cancelled) {
          setBackendConnected(true)
          if (status?.config_dir) setConfigDir(status.config_dir)
        }
      } catch {
        if (!cancelled) setBackendConnected(false)
      }

      // Best-effort config fetch. Will also fail if the backend is
      // down — the UI falls back to "—" placeholders in that case.
      try {
        const cfg = await call<VoiceTyperConfig>('get_config')
        if (!cancelled) setConfig(cfg)
      } catch {
        // intentionally leave config as null — diagnostics simply
        // show "—" until the backend comes back online.
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [call])

  const asrBackend = config
    ? `${config.asr_backend} (${config.model_size})`
    : '—'
  const device = config?.device ?? '—'
  const hotkey = config?.hotkey ?? '—'
  const microphone = config?.microphone ?? 'System Default'

  const backendStatus =
    backendConnected === null ? (
      <span className="text-(--text-muted)">Checking…</span>
    ) : (
      <StatusDot connected={backendConnected} />
    )

  return (
    <div className="min-h-full">
      <div className="mx-auto max-w-2xl space-y-8 px-6 pt-28 pb-6">
        {/* Header */}
        <div className="space-y-1 pb-5">
          <h1 className="font-sans text-2xl font-semibold tracking-tight text-(--text-primary)">
            About
          </h1>
          <p className="text-sm text-(--text-muted)">
            Diagnostic information for bug reports and support.
          </p>
        </div>

        {/* ── Diagnostics ───────────────────────────────────────── */}
        <SettingsSection
          title="Diagnostics"
          description="Include this information when filing a bug report."
        >
          <Row label="App Version" value={`v${APP_VERSION}`} />
          <Row label="Python Backend" value={backendStatus} />
          <Row label="Config Directory" value={configDir} />
          <Row label="ASR Backend" value={asrBackend} />
          <Row label="Device" value={device} />
          <Row label="Hotkey" value={hotkey} />
          <Row label="Microphone" value={microphone} />
        </SettingsSection>

        {/* ── Privacy ──────────────────────────────────────────── */}
        <SettingsSection
          title="Privacy"
          description="How your audio and data are handled."
        >
          <div className="px-3.5 py-3.5 text-sm leading-relaxed text-(--text-muted)">
            Voice Typer processes all audio locally on your device. No audio
            is sent to any server unless you explicitly configure a cloud ASR
            backend (OpenAI/Groq/Deepgram). Model weights are downloaded from
            HuggingFace on first use.
          </div>
        </SettingsSection>

        {/* ── Help ─────────────────────────────────────────────── */}
        <SettingsSection
          title="Help"
          description="Keyboard shortcuts for navigating Voice Typer."
        >
          <Row label="Start / Stop dictation" value="F2" />
          <Row label="Cancel recording (if enabled)" value="Esc" />
          <Row label="Toggle sidebar" value="Ctrl+B" />
          <Row label="Navigate" value="Tab" />
        </SettingsSection>

        {/* ── Resources ────────────────────────────────────────── */}
        <SettingsSection
          title="Resources"
          description="Source code, issue tracker, and contribution guides."
        >
          <div className="flex flex-wrap items-center gap-2 px-3.5 py-3.5">
            <Button asChild variant="outline" size="sm">
              <a href={GITHUB_REPO} target="_blank" rel="noreferrer noopener">
                GitHub Repository
              </a>
            </Button>
            <Button asChild variant="outline" size="sm">
              <a href={GITHUB_ISSUES} target="_blank" rel="noreferrer noopener">
                Report an Issue
              </a>
            </Button>
            <Button asChild variant="outline" size="sm">
              <a href={SECURITY_URL} target="_blank" rel="noreferrer noopener">
                SECURITY.md
              </a>
            </Button>
            <Button asChild variant="outline" size="sm">
              <a
                href={CONTRIBUTING_URL}
                target="_blank"
                rel="noreferrer noopener"
              >
                CONTRIBUTING.md
              </a>
            </Button>
          </div>
        </SettingsSection>
      </div>
    </div>
  )
}
