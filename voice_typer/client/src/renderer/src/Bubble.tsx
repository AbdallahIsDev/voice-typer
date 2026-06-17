import { useEffect, useRef, useState, useCallback } from 'react'

// ── Bubble bridge API (exposed by preload) ───────────────────────

interface BubbleBridge {
  onLevel: (cb: (data: { rms: number; peak: number }) => void) => () => void
  onShow: (cb: () => void) => () => void
  onHide: (cb: () => void) => () => void
  onDraggable: (cb: (draggable: boolean) => void) => () => void
  onThemeChange: (cb: (isDark: boolean) => void) => () => void
  hideComplete: () => void
  signalReady: () => void
}

declare global {
  interface Window {
    bubble?: BubbleBridge
  }
}

// ── Constants ────────────────────────────────────────────────────

const DOT_COUNT = 10                           // was 14 — removed 4 dots
const MIN_HEIGHT = 3                           // px — idle dot height
const MAX_HEIGHT = 26                          // px — peak dot height (slightly taller)
const IDLE_BREATHE_MAX = 5                     // px — upper end of idle breathing

/**
 * RMS → normalised height multiplier [0, 1].
 * Speech RMS typically lives in [0, ~0.3].  We apply a soft compressor
 * so loud transients don't peg every dot.
 */
function rmsToNorm(rms: number): number {
  return Math.min(1, rms * 5)
}

// ── Custom hook: direct-DOM animation at 60fps ────────────────────
// React state is intentionally NOT used for the per-frame dot heights.
// Instead we grab a ref to each <span> and mutate style directly from
// requestAnimationFrame — zero React re-render overhead at 60 Hz.

function useAudioLevels(dotRefs: React.RefObject<(HTMLSpanElement | null)[]>) {
  const rawLevelRef = useRef(0)       // smoothed RMS (0–1 scale)
  const speakingRef = useRef(false)
  const frameRef = useRef<number | null>(null)
  const [, forceUpdate] = useState(0)

  useEffect(() => {
    const api = window.bubble
    if (!api) return

    // ── Level listener ──────────────────────────────────────────
    const onLevel = (data: { rms: number; peak: number }) => {
      const norm = rmsToNorm(data.rms)
      // Smooth envelope: lerp toward new value so dots don't jitter.
      rawLevelRef.current = rawLevelRef.current * 0.55 + norm * 0.45
      speakingRef.current = data.rms > 0.02
    }

    const off = api.onLevel(onLevel)
    // ── Theme listener — toggle .dark class + force React re-render ──
    // The bridge fires bubble:theme from the main process when the user
    // changes theme in Settings.  We must BOTH toggle <html>'s .dark class
    // (so Tailwind dark: variants resolve) AND force a re-render (so React
    // re-evaluates className bindings on the dot spans).
    const offTheme = api.onThemeChange((isDark) => {
      document.documentElement.classList.toggle('dark', isDark)
      forceUpdate((n) => n + 1)
    })

    // ── Animation loop ───────────────────────────────────────────
    const animate = () => {
      const dots = dotRefs.current
      if (!dots) return

      const level = rawLevelRef.current
      const speaking = speakingRef.current

      if (speaking && level > 0.02) {
        // ── Speaking: center-weighted quadratic falloff ──────────────
        // Centre dots reach full height, edges taper off sharply for a
        // realistic voice visualizer look.
        const mid = (DOT_COUNT - 1) / 2
        for (let i = 0; i < DOT_COUNT; i++) {
          const el = dots[i]
          if (!el) continue
          const dist = Math.abs(i - mid) / mid                     // 0 at centre, 1 at edge
          const falloff = 1 - dist * dist * 0.65                   // quadratic: 1.0→0.35
          const h = MIN_HEIGHT + level * (MAX_HEIGHT - MIN_HEIGHT) * falloff
          el.style.height = `${Math.round(h)}px`
          // Opacity: centre brighter, edges dimmer
          el.style.opacity = `${0.5 + level * 0.5 * falloff}`
          // Do NOT set backgroundColor — Tailwind classes handle theme
        }
      } else {
        // ── Idle: subtle breathing animation ──────────────────────
        // A slow sine wave gives the dots a gentle pulse so the
        // bubble looks alive and listening, even before recording.
        for (let i = 0; i < DOT_COUNT; i++) {
          const el = dots[i]
          if (!el) continue
          const phase = i * 0.5     // per-dot phase offset for a ripple effect
          const breath = 0.5 + 0.5 * Math.sin(Date.now() / 800 + phase)
          const cur = parseFloat(el.style.height) || MIN_HEIGHT
          const breathe = MIN_HEIGHT + breath * (IDLE_BREATHE_MAX - MIN_HEIGHT)  // 3–5px
          // Decay toward the breathing target, keeping the motion smooth
          const target = Math.max(MIN_HEIGHT, Math.min(cur * 0.88 + breathe * 0.12, breathe + 0.5))
          el.style.height = `${Math.max(MIN_HEIGHT, target)}px`
          el.style.opacity = `${0.12 + breath * 0.10}`   // 0.12–0.22
          // Do NOT set backgroundColor — Tailwind classes handle theme
        }
      }

      frameRef.current = requestAnimationFrame(animate)
    }

    frameRef.current = requestAnimationFrame(animate)

    return () => {
      off()
      offTheme()
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    }
  }, [dotRefs])
}

// ── Enter / exit animation state ───────────────────────────────────

type AnimState = 'enter' | 'exit' | ''

// ── Logo (inline, self-contained so the bubble is portable) ────────

function LogoMark() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 128 109"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="Voice Typer"
    >
      <rect width="13.5631" height="108.504" rx="6.78154" className="fill-black dark:fill-white" />
      <path
        d="M77.0728 3.9668C71.5231 34.1984 57.8119 48.3888 27.125 55.7925C51.6092 61.0367 69.2379 69.3659 77.0728 104.842C84.2548 72.4507 97.6396 63.1961 128 55.7925C99.2718 49.3143 83.9284 36.9748 77.0728 3.9668Z"
        className="fill-black dark:fill-white"
      />
    </svg>
  )
}

// ── Theme sync — listens for theme change events from the bridge ──

function useThemeSync() {
  useEffect(() => {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)')
    const api = window.bubble

    const apply = (isDark: boolean) => {
      document.documentElement.classList.toggle('dark', isDark)
    }

    // When the main app sets theme_mode to 'light' or 'dark', the bridge
    // fires onThemeChange with the resolved isDark value.  Fall back to
    // OS preference when no bridge event has arrived yet.
    const onOsChange = () => apply(prefersDark.matches)
    prefersDark.addEventListener('change', onOsChange)

    // Apply initial state
    apply(prefersDark.matches)

    return () => {
      prefersDark.removeEventListener('change', onOsChange)
    }
  }, [])
}

// ── Bubble component ───────────────────────────────────────────────

export function Bubble() {
  const dotRefs = useRef<(HTMLSpanElement | null)[]>([])
  const [animState, setAnimState] = useState<AnimState>('enter')
  const [draggable, setDraggable] = useState(true)

  useThemeSync()
  useAudioLevels(dotRefs)

  // ── Enter / exit animation handlers ──────────────────────────────
  useEffect(() => {
    const api = window.bubble
    if (!api) return

    const offShow = api.onShow(() => {
      setAnimState('enter')
    })

    const offHide = api.onHide(() => {
      setAnimState('exit')
    })

    return () => {
      offShow()
      offHide()
    }
  }, [])

  // ── Listen for draggable state ───────────────────────────────────
  useEffect(() => {
    const api = window.bubble
    if (!api) return

    const off = api.onDraggable((d) => setDraggable(d))
    return off
  }, [])

  // ── Animation-end callback ──────────────────────────────────────
  // When the exit CSS transition completes, tell the main process
  // it's safe to actually hide() the BrowserWindow.
  const handleAnimEnd = useCallback(() => {
    if (animState === 'exit') {
      setAnimState('')
      window.bubble?.hideComplete?.()
    } else if (animState === 'enter') {
      setAnimState('')
    }
  }, [animState])

  // ── Build dot spans ──────────────────────────────────────────────
  const dots = Array.from({ length: DOT_COUNT }, (_, i) => i)

  return (
    <div
      className={`
        inline-flex items-center gap-3 rounded-full
        border border-zinc-200 dark:border-white/10
        bg-white dark:bg-zinc-900
        px-4 py-2.5
        ${animState === 'enter' ? 'animate-bubble-enter' : ''}
        ${animState === 'exit'  ? 'animate-bubble-exit'  : ''}
      `}
      style={
        {
          WebkitAppRegion: draggable ? 'drag' : 'no-drag',
          transition: 'opacity 200ms ease, transform 200ms ease',
        } as React.CSSProperties
      }
      onAnimationEnd={handleAnimEnd}
    >
      <LogoMark />

      {/* ── Dot visualiser ──────────────────────────────────── */}
      <div className="flex items-center gap-[3px]">
        {dots.map((i) => (
          <span
            key={i}
            ref={(el) => { dotRefs.current[i] = el }}
            className="inline-block w-[3px] rounded-full bg-zinc-900 dark:bg-white"
            style={{ height: MIN_HEIGHT, opacity: 0.12 }}
          />
        ))}
      </div>
    </div>
  )
}

export default Bubble
