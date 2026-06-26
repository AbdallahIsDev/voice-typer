import type { ShareStats } from '@/types/stats'

interface StatsShareImageProps {
  stats: ShareStats
}

/**
 * StatsShareImage — a beautifully designed shareable stats card.
 *
 * This component is rendered off-screen and captured as a PNG image
 * by html-to-image when the user clicks "Share Stats".
 *
 * The design is inspired by popular voice transcription apps and features:
 *   - A gradient background with a waveform SVG decoration
 *   - Words Per Minute as the hero stat with speed comparison
 *   - Minutes Saved vs average typing speed
 *   - Most Used Mode (Cloud vs Offline)
 *   - App branding at the bottom
 *
 * To edit the design in the future: modify the JSX/CSS below.
 * The layout, colors, typography, and waveform are all here.
 */
export function StatsShareImage({ stats }: StatsShareImageProps) {
  return (
    <div
      style={{
        width: '600px',
        height: '500px',
        position: 'relative',
        overflow: 'hidden',
        fontFamily: "'Geist Variable', 'Inter', system-ui, -apple-system, sans-serif",
      }}
    >
      {/* ── Background gradient ──────────────────────────────── */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: 'linear-gradient(135deg, #0f0a1a 0%, #1a1040 40%, #0d1b2a 100%)',
        }}
      />

      {/* ── Waveform decoration (top area) ──────────────────── */}
      <svg
        viewBox="0 0 600 120"
        preserveAspectRatio="none"
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '100%',
          height: '120px',
          opacity: 0.15,
        }}
      >
        <defs>
          <linearGradient id="waveGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#8b5cf6" />
            <stop offset="50%" stopColor="#6366f1" />
            <stop offset="100%" stopColor="#06b6d4" />
          </linearGradient>
        </defs>
        {Array.from({ length: 48 }, (_, i) => {
          const barWidth = 600 / 48 - 3
          const heights = [28, 42, 38, 55, 46, 62, 50, 38, 44, 58, 48, 34, 40, 56, 52, 36, 30, 48, 60, 44, 38, 54, 48, 32, 36, 50, 58, 42, 34, 48, 54, 38, 44, 60, 46, 34, 52, 56, 40, 30, 48, 54, 38, 44, 58, 50, 36, 42]
          const h = heights[i % heights.length]
          return (
            <rect
              key={i}
              x={i * (600 / 48) + 1.5}
              y={60 - h / 2}
              width={barWidth}
              height={h}
              rx={2}
              fill="url(#waveGrad)"
              opacity={0.6 + (h / 60) * 0.4}
            />
          )
        })}
      </svg>

      {/* ── Glow spot ────────────────────────────────────────── */}
      <div
        style={{
          position: 'absolute',
          top: '40px',
          left: '50%',
          transform: 'translateX(-50%)',
          width: '300px',
          height: '300px',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 70%)',
          pointerEvents: 'none',
        }}
      />

      {/* ── Content ─────────────────────────────────────────── */}
      <div
        style={{
          position: 'relative',
          zIndex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          padding: '0 40px',
        }}
      >
        {/* ── Hero: WPM ─────────────────────────────────────── */}
        <div style={{ textAlign: 'center', marginBottom: '36px' }}>
          <div
            style={{
              fontSize: '72px',
              fontWeight: 800,
              letterSpacing: '-0.03em',
              lineHeight: 1,
              color: '#ffffff',
              marginBottom: '4px',
            }}
          >
            {stats.wpmDisplay}
            <span
              style={{
                fontSize: '28px',
                fontWeight: 600,
                color: 'rgba(255,255,255,0.5)',
                marginLeft: '8px',
              }}
            >
              WPM
            </span>
          </div>
          <div
            style={{
              fontSize: '14px',
              color: 'rgba(255,255,255,0.45)',
              fontWeight: 500,
              letterSpacing: '0.01em',
            }}
          >
            {stats.fasterThanAvg}
          </div>
        </div>

        {/* ── Stats row ─────────────────────────────────────── */}
        <div style={{ display: 'flex', gap: '16px', marginBottom: '40px' }}>
          {/* Minutes Saved */}
          <div
            style={{
              flex: 1,
              minWidth: '180px',
              padding: '20px 24px',
              borderRadius: '16px',
              background: 'rgba(255,255,255,0.06)',
              border: '1px solid rgba(255,255,255,0.08)',
              textAlign: 'center',
            }}
          >
            <div
              style={{
                fontSize: '36px',
                fontWeight: 700,
                color: '#a78bfa',
                lineHeight: 1,
                marginBottom: '4px',
              }}
            >
              {stats.minutesSavedDisplay}
              <span
                style={{
                  fontSize: '16px',
                  fontWeight: 500,
                  color: 'rgba(255,255,255,0.4)',
                  marginLeft: '4px',
                }}
              >
                min
              </span>
            </div>
            <div
              style={{
                fontSize: '12px',
                color: 'rgba(255,255,255,0.4)',
                fontWeight: 500,
              }}
            >
              saved vs typing
            </div>
          </div>

          {/* Most Used Mode */}
          <div
            style={{
              flex: 1,
              minWidth: '180px',
              padding: '20px 24px',
              borderRadius: '16px',
              background: 'rgba(255,255,255,0.06)',
              border: '1px solid rgba(255,255,255,0.08)',
              textAlign: 'center',
            }}
          >
            <div
              style={{
                fontSize: '20px',
                fontWeight: 700,
                color: '#67e8f9',
                lineHeight: 1,
                marginBottom: '4px',
              }}
            >
              {stats.modeDisplay}
            </div>
            <div
              style={{
                fontSize: '12px',
                color: 'rgba(255,255,255,0.4)',
                fontWeight: 500,
              }}
            >
              {stats.modeDetail}
            </div>
          </div>
        </div>

        {/* ── Branding ──────────────────────────────────────── */}
        <div
          style={{
            fontSize: '12px',
            color: 'rgba(255,255,255,0.25)',
            fontWeight: 500,
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
            marginTop: 'auto',
            paddingBottom: '24px',
          }}
        >
          Voice Typer Stats
        </div>
      </div>
    </div>
  )
}
