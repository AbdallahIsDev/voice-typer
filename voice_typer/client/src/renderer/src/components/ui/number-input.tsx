import * as React from "react"

import { cn } from "#utils"

interface NumberInputProps
  extends Omit<React.ComponentProps<"input">, "type" | "onChange"> {
  min?: number
  max?: number
  step?: number
  onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void
}

function NumberInput({
  className,
  min,
  max,
  step = 1,
  value,
  onChange,
  ...props
}: NumberInputProps) {
  const [showButtons, setShowButtons] = React.useState(false)

  const stepValue = (dir: 1 | -1) => {
    const current = Number(value) || 0
    const raw = current + dir * step
    let clamped = raw
    if (min !== undefined) clamped = Math.max(min, clamped)
    if (max !== undefined) clamped = Math.min(max, clamped)
    if (onChange) {
      const fakeEvent = {
        target: { value: String(clamped) },
        currentTarget: { value: String(clamped) },
      } as React.ChangeEvent<HTMLInputElement>
      onChange(fakeEvent)
    }
  }

  return (
    <div
      className="relative inline-flex items-center"
      onMouseEnter={() => setShowButtons(true)}
      onMouseLeave={() => setShowButtons(false)}
    >
      <input
        type="number"
        data-slot="input"
        className={cn(
          "h-9 w-full min-w-0 rounded-3xl border border-transparent bg-input/50 pl-3 pr-6 py-1 text-base transition-[color,box-shadow,background-color] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none [-moz-appearance:textfield]",
          className,
        )}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={onChange}
        {...props}
      />

      {/* Custom up/down buttons — appear on hover */}
      <div
        className={cn(
          "absolute right-1 top-1/2 -translate-y-1/2 flex flex-col transition-opacity duration-150",
          showButtons ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
      >
        <button
          type="button"
          tabIndex={-1}
          aria-label="Increase"
          onClick={(e) => {
            e.preventDefault()
            stepValue(1)
          }}
          className="flex h-[9px] w-[14px] items-center justify-center rounded-[2px] text-(--text-muted) hover:text-(--text-primary) hover:bg-(--accent-soft) transition-colors"
        >
          <svg width="8" height="5" viewBox="0 0 8 5" fill="none">
            <path d="M4 0L8 5H0L4 0Z" fill="currentColor" />
          </svg>
        </button>
        <button
          type="button"
          tabIndex={-1}
          aria-label="Decrease"
          onClick={(e) => {
            e.preventDefault()
            stepValue(-1)
          }}
          className="flex h-[9px] w-[14px] items-center justify-center rounded-[2px] text-(--text-muted) hover:text-(--text-primary) hover:bg-(--accent-soft) transition-colors"
        >
          <svg width="8" height="5" viewBox="0 0 8 5" fill="none">
            <path d="M4 5L0 0H8L4 5Z" fill="currentColor" />
          </svg>
        </button>
      </div>
    </div>
  )
}

export { NumberInput }
