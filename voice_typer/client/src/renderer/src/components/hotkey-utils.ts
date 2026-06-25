export const KEY_CODE_TO_PYNPUT: Record<string, string> = {
  F1: 'f1', F2: 'f2', F3: 'f3', F4: 'f4', F5: 'f5', F6: 'f6',
  F7: 'f7', F8: 'f8', F9: 'f9', F10: 'f10', F11: 'f11', F12: 'f12',
  F13: 'f13', F14: 'f14', F15: 'f15', F16: 'f16', F17: 'f17',
  F18: 'f18', F19: 'f19',
  Space: 'space',
  Enter: 'enter',
  Tab: 'tab',
  Escape: 'esc',
  Backspace: 'backspace',
  Insert: 'insert',
  Delete: 'delete',
  Home: 'home',
  End: 'end',
  PageUp: 'page_up',
  PageDown: 'page_down',
  CapsLock: 'caps_lock',
  NumLock: 'num_lock',
  ScrollLock: 'scroll_lock',
  PrintScreen: 'print_screen',
  Pause: 'pause',
  ContextMenu: 'menu',
  ArrowUp: 'up',
  ArrowDown: 'down',
  ArrowLeft: 'left',
  ArrowRight: 'right',
  MediaPlay: 'media_play_pause',
  MediaStop: 'media_stop',
  MediaTrackNext: 'media_next',
  MediaTrackPrevious: 'media_previous',
  AudioVolumeMute: 'volume_mute',
  AudioVolumeUp: 'volume_up',
  AudioVolumeDown: 'volume_down',
}

export const MODIFIER_KEYS = [
  'ctrl', 'ctrl_l', 'ctrl_r',
  'shift', 'shift_l', 'shift_r',
  'alt', 'alt_l', 'alt_r', 'alt_gr',
  'cmd', 'cmd_l', 'cmd_r', 'win',
] as const

export const MODIFIER_CODE_TO_PYNPUT: Record<string, string> = {
  ControlLeft: 'ctrl',
  ControlRight: 'ctrl',
  ShiftLeft: 'shift',
  ShiftRight: 'shift',
  AltLeft: 'alt',
  AltRight: 'alt',
  MetaLeft: 'cmd',
  MetaRight: 'cmd',
}

export const SINGLE_KEY_PRESETS: { value: string; label: string }[] = [
  { value: 'f2', label: 'F2' },
  { value: 'f3', label: 'F3' },
  { value: 'f4', label: 'F4' },
  { value: 'f5', label: 'F5' },
  { value: 'f6', label: 'F6' },
  { value: 'f7', label: 'F7' },
  { value: 'f8', label: 'F8' },
  { value: 'f9', label: 'F9' },
  { value: 'f10', label: 'F10' },
  { value: 'f11', label: 'F11' },
  { value: 'f12', label: 'F12' },
  { value: 'caps_lock', label: 'Caps Lock' },
  { value: 'print_screen', label: 'Print Screen' },
  { value: 'scroll_lock', label: 'Scroll Lock' },
  { value: 'pause', label: 'Pause/Break' },
  { value: 'insert', label: 'Insert' },
  { value: 'home', label: 'Home' },
  { value: 'page_up', label: 'Page Up' },
  { value: 'page_down', label: 'Page Down' },
]

export const COMBO_PRESETS: { value: string; label: string }[] = [
  { value: '<ctrl>+<alt>+v', label: 'Ctrl+Alt+V (default)' },
  { value: '<ctrl>+<shift>+v', label: 'Ctrl+Shift+V' },
  { value: '<ctrl>+<alt>+r', label: 'Ctrl+Alt+R' },
  { value: '<ctrl>+<shift>+r', label: 'Ctrl+Shift+R' },
  { value: '<cmd>+<shift>+v', label: 'Cmd+Shift+V (macOS)' },
  { value: '<ctrl>+v', label: 'Ctrl+V (conflicts with paste)' },
]

export function formatHotkeyLabel(hotkey: string): string {
  if (!hotkey) return 'None'
  return hotkey
    .split('+')
    .map((part) => {
      const key = part.replace(/[<>]/g, '').trim()
      const displayMap: Record<string, string> = {
        ctrl: 'Ctrl', ctrl_l: 'Ctrl', ctrl_r: 'Ctrl',
        shift: 'Shift', shift_l: 'Shift', shift_r: 'Shift',
        alt: 'Alt', alt_l: 'Alt', alt_r: 'Alt', alt_gr: 'AltGr',
        cmd: 'Cmd', cmd_l: 'Cmd', cmd_r: 'Cmd', win: 'Win',
        space: 'Space', enter: 'Enter', tab: 'Tab', esc: 'Esc',
        caps_lock: 'Caps Lock', num_lock: 'Num Lock',
        scroll_lock: 'Scroll Lock', print_screen: 'Print Screen',
        pause: 'Pause', insert: 'Insert', delete: 'Delete',
        home: 'Home', end: 'End', page_up: 'Page Up',
        page_down: 'Page Down',
        up: '\u2191', down: '\u2193', left: '\u2190', right: '\u2192',
      }
      if (displayMap[key]) return displayMap[key]
      if (/^f\d{1,2}$/.test(key)) return key.toUpperCase()
      if (key.length === 1) return key.toUpperCase()
      return key.charAt(0).toUpperCase() + key.slice(1)
    })
    .join('+')
}

export function validateHotkey(
  hotkey: string,
  mode: 'single' | 'combo',
): string | null {
  if (!hotkey || !hotkey.trim()) {
    return 'Hotkey is empty'
  }
  const parts = hotkey
    .split('+')
    .map((p) => p.replace(/[<>]/g, '').trim())
    .filter(Boolean)
  if (parts.length === 0) {
    return 'Hotkey has no keys'
  }
  if (mode === 'single') {
    if (parts.length > 1) {
      return 'Dictation key must be a single key (no modifiers). Use the re-paste key for combos.'
    }
    return null
  }
  const lastKey = parts[parts.length - 1]
  if (MODIFIER_KEYS.includes(lastKey as typeof MODIFIER_KEYS[number])) {
    return 'Combo must end with a non-modifier key (e.g. Ctrl+Alt+V, not just Ctrl)'
  }
  return null
}
