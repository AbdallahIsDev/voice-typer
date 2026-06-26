import { Switch } from '@/components/ui/switch'

interface NoiseFilterRowProps {
  /** Display label for the filter */
  label: string
  /** Description shown below the label */
  description: string
  /** Whether the switch is checked */
  checked: boolean
  /** Whether the switch is disabled */
  disabled: boolean
  /** Called when the user toggles the switch */
  onChange: (checked: boolean) => void
  /** Accessible label for the switch */
  ariaLabel: string
}

export function NoiseFilterRow({
  label,
  description,
  checked,
  disabled,
  onChange,
  ariaLabel,
}: NoiseFilterRowProps) {
  return (
    <div className="flex items-center justify-between px-4 py-2 pl-10">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-medium text-(--text-primary)">{label}</p>
        <p className="text-xs text-(--text-muted)">{description}</p>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onChange}
        disabled={disabled}
        aria-label={ariaLabel}
      />
    </div>
  )
}
