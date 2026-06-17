import { useEffect, useRef, useState } from 'react'

interface BubbleBridge {
  onLevel: (callback: (data: { rms: number; peak: number }) => void) => () => void
}

declare global {
  interface Window {
    bubble?: BubbleBridge
  }
}

const BAR_COUNT = 28
const TARGET_FPS = 60
const FRAME_MS = 1000 / TARGET_FPS

function rmsToHeight(rms: number): number {
  // Audio RMS in [0, ~0.3] for speech; map to [0, 1] with a soft
  // compressor so loud sounds don't peg the bars.
  const v = Math.min(1, rms * 6)
  return v
}

function useLevels() {
  const [levels, setLevels] = useState<number[]>(() =>
    new Array(BAR_COUNT).fill(0).map(() => 0.04 + Math.random() * 0.02),
  )
  const [speaking, setSpeaking] = useState(false)
  const idxRef = useRef(0)
  const rafRef = useRef<number | null>(null)
  const nextTickRef = useRef(0)

  useEffect(() => {
    const api = window.bubble
    if (!api) return

    // Push the latest sample into a rolling window.  The animation
    // loop drains this window on requestAnimationFrame and advances
    // the index, producing a left-to-right scrolling waveform.
    const onLevel = (data: { rms: number; peak: number }) => {
      const h = rmsToHeight(data.rms)
      setLevels((prev) => {
        const next = prev.slice()
        next[idxRef.current % BAR_COUNT] = h
        idxRef.current = (idxRef.current + 1) % BAR_COUNT
        return next
      })
      setSpeaking(data.rms > 0.012)
    }
    const off = api.onLevel(onLevel)

    const tick = (t: number) => {
      if (t >= nextTickRef.current) {
        nextTickRef.current = t + FRAME_MS
        // Idle decay: while not receiving new samples, ease every bar
        // toward a low baseline so the waveform looks "alive" without
        // speech.
        setLevels((prev) => {
          const out = prev.slice()
          for (let i = 0; i < out.length; i++) {
            const v = out[i]
            if (v > 0.06) out[i] = v * 0.92 + 0.05
            else out[i] = v * 0.96 + 0.02
          }
          return out
        })
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => {
      off()
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return { levels, speaking }
}

function Waveform({ levels, speaking }: { levels: number[]; speaking: boolean }) {
  const w = 220
  const h = 56
  const barW = w / BAR_COUNT
  const gap = Math.max(1, barW * 0.25)
  const fill = barW - gap

  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      className="select-none"
      aria-hidden
    >
      <defs>
        <linearGradient id="wf-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#818cf8" stopOpacity="0.95" />
          <stop offset="100%" stopColor="#4f46e5" stopOpacity="0.6" />
        </linearGradient>
      </defs>
      {levels.map((v, i) => {
        const norm = Math.max(0.04, Math.min(1, v))
        const bh = norm * (h - 6)
        const y = (h - bh) / 2
        const x = i * barW + gap / 2
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={fill}
            height={bh}
            rx={Math.min(3, fill / 2)}
            ry={Math.min(3, fill / 2)}
            fill="url(#wf-grad)"
            opacity={speaking ? 1 : 0.55}
          />
        )
      })}
    </svg>
  )
}

function MicIcon({ active }: { active: boolean }) {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={active ? 'text-indigo-500' : 'text-zinc-400'}
      aria-label="microphone"
    >
      <rect x="9" y="2" width="6" height="13" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <line x1="12" y1="18" x2="12" y2="22" />
    </svg>
  )
}

function LogoMark() {
  // Inline copy of the project's logo (matches Logo.tsx in the main
  // app).  Kept here so the bubble is self-contained.
  return (
    <svg
      width="28"
      height="28"
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

export function Bubble() {
  const { levels, speaking } = useLevels()
  console.log(`[bubble] render levels=${levels.length} speaking=${speaking} dom=${typeof document !== 'undefined'}`)
  return (
    <div
      className="flex h-screen w-screen items-center justify-center p-2"
      style={{ background: '#0a0a0c' }}
    >
      <div
        className="flex items-center gap-4 rounded-2xl border border-white/10 bg-zinc-900 px-5 py-4 shadow-2xl ring-1 ring-black/40"
        style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
      >
        <div className="flex flex-col items-center gap-1">
          <LogoMark />
        </div>
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-zinc-300">
            <MicIcon active={speaking} />
            <span className={speaking ? 'text-indigo-300' : 'text-zinc-400'}>
              {speaking ? 'Listening' : 'Recording'}
            </span>
          </div>
          <Waveform levels={levels} speaking={speaking} />
        </div>
      </div>
    </div>
  )
}

export default Bubble
