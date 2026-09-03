# Focus Indicators — Visibility Is Non-Negotiable

**Status**: Decided (2026-08-28, after a programmatic WCAG contrast audit)
**Decision owner**: voice-typer UX
**Supersedes**: none (supersedes ad-hoc per-component focus styling)
**Related code**:
- `voice_typer/client/src/renderer/src/components/ui/__tests__/focus-ring-contrast.test.tsx` — the executable contract
- `voice_typer/client/src/renderer/src/components/common/SearchField.tsx` — the pointer-modality suppression pattern
- `voice_typer/client/src/renderer/src/components/common/Kbd.tsx` — tooltip-context contrast for keycaps
- Consumer primitives: `Button`, `Input`, `SelectTrigger` (full-opacity ring + `ring-3`)

## Context

A programmatic WCAG audit found two defects:

1. A "make it prettier" edit had changed the focus ring to
   `focus-visible:ring-ring/30`. Composited against the themes' backgrounds,
   that alpha ring measured **1.15:1 – 2.45:1 contrast across all 12 themes**
   — far below WCAG 1.4.11's 3:1 minimum, i.e. effectively invisible
   everywhere.
2. Text inputs always match `:focus-visible` on mouse click as well as
   keyboard (browsers intentionally mark text boxes as needing input), so the
   full ring painted on every click into a search field — reported as a UX
   defect.

## Decision

Three binding rules:

1. **Never remove or hide the keyboard focus indicator** — no blanket
   `outline: none`, no conditional `display:none` on the ring. WCAG 2.4.7
   (Level A) requires a visible indicator for keyboard/AT users, who have no
   mouse-cursor equivalent. If a focus style looks wrong, replace it with a
   better one.
2. **Full-opacity color, adequate thickness.** Interactive primitives keep
   `focus-visible:ring-ring` at **full opacity** — the theme files tune
   `--ring` for ≥3:1 contrast; an alpha modifier (`ring-ring/30`) discards
   that tuning. Thickness is `ring-3` (the app standard), with `ring-2` as
   the acceptable floor (WCAG 2.4.13's ≥2px area). Thickness may be tuned
   down to 2px, never below; alpha may never be reduced.
3. **Pointer-modality tracking for text inputs.** CSS alone cannot separate
   mouse from keyboard on text boxes, so click-ring suppression on text
   inputs is implemented by the shared `SearchField` pattern: `pointerdown`
   sets pointer-active, Tab/Arrow keydown clears it, blur resets. While
   pointer-active the field gets a subtle `focus:border-ring/60` border tint
   (the caret already marks it active) and `focus-visible:ring-0`;
   keyboard/AT focus gets the clear full-opacity ring.

## Why

- **Regulatory floor, not taste.** WCAG 2.4.7 is Level A; removing the
  indicator makes the app unusable for keyboard and switch users.
- **Contrast math is unforgiving.** 30% alpha over a dark background
  composites to ~1.5:1 — below the 3:1 minimum for non-text contrast
  (1.4.11) and below what low-vision users can perceive. The alpha was
  measured, not assumed, across all 12 themes.
- **Mouse UX and keyboard accessibility are not in conflict** once modality
  is tracked: caret + border tint mark click-focus; the ring remains
  exclusive to keyboard navigation.

## Alternatives considered

- **`ring-ring/30` with a thicker ring.** Rejected: measured 1.15:1–2.45:1 —
  no thickness compensates for a nearly invisible color.
- **`outline: none` + box-shadow on `:focus` only.** Rejected: hides the
  indicator from keyboard users on mousedown states and is the canonical
  accessibility antipattern.
- **Pure `:focus:not(:focus-visible)` suppression on text inputs.** Rejected:
  text boxes match `:focus-visible` on click too, so the heavy ring still
  painted on every click — the reported defect.
- **Suppress the ring app-wide for mouse users.** Rejected: breaks keyboard
  users' ability to see where focus is.

## User impact

- Keyboard users see a clearly visible 3px full-opacity ring in every theme;
  the ring passes 3:1 contrast in all 12 themes (audited + test-pinned).
- Mouse users clicking into the (global) search field get a calm border tint
  and caret instead of a heavy ring; Tab/arrow navigation restores the ring.
- Keycaps inside tooltips keep foreground-based contrast tokens, so
  shortcut chips remain readable on every theme (never
  `text-background`-style dark-on-dark).

## Test coverage

`focus-ring-contrast.test.tsx` pins the contract on the interactive
primitives: full-opacity `focus-visible:ring-ring` (no alpha modifier),
`ring-3` thickness, and the `focus-visible:` qualifier. Any "prettier" ring
edit fails this suite.
