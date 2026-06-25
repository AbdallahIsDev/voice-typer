import { useState, useEffect, useCallback, useRef } from 'react'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { HugeiconsIcon } from '@hugeicons/react'
import { KeyboardIcon, Cancel01Icon } from '@hugeicons/core-free-icons'
import {
  KEY_CODE_TO_PYNPUT,
  MODIFIER_CODE_TO_PYNPUT,
  SINGLE_KEY_PRESETS,
  COMBO_PRESETS,
  formatHotkeyLabel,
  validateHotkey,
} from './hotkey-utils'

interface HotkeyPickerProps {
  value: string
  onChange: (hotkey: string) => void
  mode: 'single' | 'combo'
  className?: string
  'aria-label'?: string
}

export function HotkeyPicker({
  value,
  onChange,
  mode,
  className,
  'aria-label': ariaLabel = 'Hotkey picker',
}: HotkeyPickerProps) {
  const [recording, setRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!recording) return

      e.preventDefault()
      e.stopPropagation()

      if (e.key === 'Escape') {
        setRecording(false)
        setError(null)
        return
      }

      const isModifier = e.code in MODIFIER_CODE_TO_PYNPUT
      if (mode === 'single') {
        if (isModifier) return
        const pynputName = KEY_CODE_TO_PYNPUT[e.key]
        if (!pynputName) {
          setError(`Key "${e.key}" is not supported as a hotkey. Try F2-F12, Caps Lock, or Print Screen.`)
          return
        }
        const newHotkey = `<${pynputName}>`
        const validationError = validateHotkey(newHotkey, mode)
        if (validationError) {
          setError(validationError)
          return
        }
        onChange(newHotkey)
        setError(null)
        setRecording(false)
        return
      }

      if (isModifier) return

      const mods: string[] = []
      if (e.ctrlKey) mods.push('ctrl')
      if (e.shiftKey) mods.push('shift')
      if (e.altKey) mods.push('alt')
      if (e.metaKey) mods.push('cmd')

      const pynputName = KEY_CODE_TO_PYNPUT[e.key]
      if (!pynputName) {
        setError(`Key "${e.key}" is not supported. Try letters, numbers, F-keys, or Space.`)
        return
      }

      const parts = [...mods, pynputName]
      const newHotkey = parts.map((p) => `<${p}>`).join('+')
      const validationError = validateHotkey(newHotkey, mode)
      if (validationError) {
        setError(validationError)
        return
      }
      onChange(newHotkey)
      setError(null)
      setRecording(false)
    },
    [recording, mode, onChange],
  )

  useEffect(() => {
    if (!recording) return
    window.addEventListener('keydown', handleKeyDown, true)
    return () => {
      window.removeEventListener('keydown', handleKeyDown, true)
    }
  }, [recording, handleKeyDown])

  const startRecording = useCallback(() => {
    setRecording(true)
    setError(null)
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
    timeoutRef.current = setTimeout(() => {
      setRecording(false)
      setError('Recording timed out \u2014 try again')
    }, 5000)
  }, [])

  const cancelRecording = useCallback(() => {
    setRecording(false)
    setError(null)
    if (timeoutRef.current) clearTimeout(timeoutRef.current)
  }, [])

  const presets = mode === 'single' ? SINGLE_KEY_PRESETS : COMBO_PRESETS
  const presetValue = mode === 'single'
    ? value.replace(/[<>]/g, '')
    : value

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button
          variant={recording ? 'default' : 'outline'}
          size="sm"
          onClick={recording ? cancelRecording : startRecording}
          className={cn('gap-2 font-mono', className)}
          aria-label={recording ? `Cancel recording \u2014 ${ariaLabel}` : `Record new hotkey \u2014 ${ariaLabel}`}
        >
          <HugeiconsIcon icon={recording ? Cancel01Icon : KeyboardIcon} strokeWidth={1.625} className="h-4 w-4" />
          {recording ? (
            <span className="animate-pulse">Press a key\u2026</span>
          ) : (
            <span>{formatHotkeyLabel(value) || 'None'}</span>
          )}
        </Button>

        <Select
          value={presetValue}
          onValueChange={(v) => {
            const newValue = mode === 'single' ? `<${v}>` : v
            const validationError = validateHotkey(newValue, mode)
            if (validationError) {
              setError(validationError)
            } else {
              setError(null)
              onChange(newValue)
            }
          }}
        >
          <SelectTrigger className="w-40" aria-label={`Preset hotkeys \u2014 ${ariaLabel}`}>
            <SelectValue placeholder="Presets\u2026" />
          </SelectTrigger>
          <SelectContent>
            {presets.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
      {recording && (
        <p className="text-xs text-(--text-muted)">
          Press a key{mode === 'combo' ? ' combination (hold modifiers, then press a key)' : ''}.
          Press <kbd className="rounded border border-border px-1">Esc</kbd> to cancel.
        </p>
      )}
    </div>
  )
}
