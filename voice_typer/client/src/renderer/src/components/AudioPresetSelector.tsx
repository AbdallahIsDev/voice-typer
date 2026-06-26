import { HugeiconsIcon } from '@hugeicons/react'
import { FilterIcon } from '@hugeicons/core-free-icons'
import { Switch } from '@/components/ui/switch'
import { NoiseFilterRow } from '@/components/NoiseFilterRow'

export type AudioPreset = 'none' | 'recommended' | 'noisy_room' | 'studio' | 'custom'

export interface NoiseFilterState {
  noise_filter_enabled: boolean
  noise_filter_highpass: boolean
  noise_filter_gate: boolean
  noise_filter_rnnoise: boolean
  noise_filter_post_capture: boolean
}

interface AudioPresetSelectorProps {
  preset: AudioPreset
  filters: NoiseFilterState
  showAdvanced: boolean
  onPresetChange: (preset: AudioPreset) => void
  onToggleAdvanced: () => void
  onFilterChange: (key: keyof NoiseFilterState, value: boolean) => void
}

const PRESET_OPTIONS: { value: AudioPreset; label: string; description: string }[] = [
  { value: 'none', label: 'None', description: 'No audio processing' },
  { value: 'recommended', label: 'Recommended', description: 'Noise filter + RNNoise denoising' },
  { value: 'noisy_room', label: 'Noisy Room', description: 'Full enhancement: gate + high-pass + RNNoise' },
  { value: 'studio', label: 'Studio', description: 'High-pass filter only' },
  { value: 'custom', label: 'Custom', description: 'Manual control over each filter' },
]

const FILTER_ROWS: {
  key: keyof NoiseFilterState
  label: string
  description: string
  ariaLabel: string
}[] = [
  {
    key: 'noise_filter_highpass',
    label: 'High-Pass Filter',
    description: 'Removes low-frequency rumble (HVAC, traffic, fans)',
    ariaLabel: 'High-Pass Filter',
  },
  {
    key: 'noise_filter_gate',
    label: 'Noise Gate',
    description: 'Reduces background noise below a threshold',
    ariaLabel: 'Noise Gate',
  },
  {
    key: 'noise_filter_rnnoise',
    label: 'RNNoise (Neural)',
    description: 'AI-based real-time denoising',
    ariaLabel: 'RNNoise',
  },
  {
    key: 'noise_filter_post_capture',
    label: 'Post-Capture Cleanup',
    description: 'Spectral noise reduction after stop',
    ariaLabel: 'Post-Capture Cleanup',
  },
]

export function AudioPresetSelector({
  preset,
  filters,
  showAdvanced,
  onPresetChange,
  onToggleAdvanced,
  onFilterChange,
}: AudioPresetSelectorProps) {
  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <button
        type="button"
        className="flex w-full items-center justify-between px-4 py-2.5 text-xs font-medium text-(--text-primary) hover:bg-(--accent)/5 transition-colors cursor-pointer"
        onClick={onToggleAdvanced}
        aria-expanded={showAdvanced}
      >
        <span className="flex items-center gap-2 font-medium tracking-wide">
          <HugeiconsIcon
            icon={FilterIcon}
            strokeWidth={2.25}
            className="h-4 w-4 text-(--text-muted)"
          />
          Audio Enhancement
        </span>
        <span className="text-xs font-medium tracking-wide text-(--text-muted)">
          {preset === 'custom' ? 'Custom' : preset.replace('_', ' ')}
        </span>
      </button>

      {showAdvanced && (
        <div className="divide-y divide-border border-t border-border">
          {/* Preset selector */}
          <div className="px-4 py-3 space-y-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-(--text-muted)">
              Preset
            </p>
            <div className="grid grid-cols-1 gap-1.5">
              {PRESET_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`flex items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors cursor-pointer ${
                    preset === option.value
                      ? 'bg-primary/10 border border-primary/30 text-(--text-primary)'
                      : 'bg-(--bg-subtle) border border-transparent text-(--text-muted) hover:bg-(--accent)/5'
                  }`}
                  onClick={() => onPresetChange(option.value)}
                >
                  <span className={`w-2 h-2 rounded-full shrink-0 ${
                    preset === option.value ? 'bg-primary' : 'bg-(--text-muted)/30'
                  }`} />
                  <div>
                    <span className="font-medium">{option.label}</span>
                    <span className="ml-1 text-[10px] opacity-70">{option.description}</span>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Custom mode: individual filter toggles */}
          {preset === 'custom' && (
            <div className="divide-y divide-border">
              <div className="flex items-center justify-between px-4 py-2">
                <div className="flex flex-col gap-1">
                  <p className="text-xs font-medium text-(--text-primary)">
                    Noise Filter
                  </p>
                  <p className="text-xs text-(--text-muted)">
                    Master enable for all audio enhancements
                  </p>
                </div>
                <Switch
                  checked={filters.noise_filter_enabled}
                  onCheckedChange={(checked) =>
                    onFilterChange('noise_filter_enabled', checked)
                  }
                  aria-label="Noise Filter"
                />
              </div>

              {FILTER_ROWS.map((row) => (
                <NoiseFilterRow
                  key={row.key}
                  label={row.label}
                  description={row.description}
                  checked={filters[row.key]}
                  disabled={!filters.noise_filter_enabled}
                  onChange={(checked) => onFilterChange(row.key, checked)}
                  ariaLabel={row.ariaLabel}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
