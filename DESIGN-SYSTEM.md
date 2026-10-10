# Design System — renderer (px / hex reference)

Everything below is reported in **pixels** and **hex**, with the value the codebase
actually stores next to it. Source of truth:
`voice_typer/client/src/renderer/src/index.css` (tokens),
`voice_typer/client/src/renderer/src/themes/*.ts` (presets),
Tailwind v4 defaults (`node_modules/tailwindcss/theme.css`).

Hex values are computed, not eyeballed: `node scripts/oklch-to-hex.mjs` converts the
oklch tokens to sRGB and prints the WCAG contrast table.

---

## 0. Conversion rules (standing)

When you give me a value in px or hex, I convert it and write it in the format the
file already uses. The format never changes — only the value.

| You say | I write | How |
|---|---|---|
| `8px` padding | `p-2` | Tailwind spacing unit = 4px → `px ÷ 4` = class number. 8px → `2`. Odd values are fine: 10px → `p-2.5`, 14px → `gap-3.5` |
| `8px` in raw CSS | `0.5rem` | `px ÷ 16` |
| `12px` font | `text-xs` | or `text-[0.75rem]` if it falls off the scale |
| `13px` font | `text-xs-plus` | the app's ONE added type step, between `text-xs` (12px) and `text-sm` (14px). Declared as `--text-xs-plus` in `index.css`'s `@theme` block, so it is a real utility — never write `text-[0.8125rem]` |
| `#1447e6` | `oklch(0.488 0.243 264.376)` | sRGB → linear → OKLab → OKLCH (`node scripts/oklch-to-hex.mjs "#1447e6"`) |
| `50%` opacity | `/50` modifier | `bg-primary/50` |

Root font size is `16px × --font-scale` (default 1), so 1rem = 16px everywhere and
every rem-based value scales with the user's text-size setting.

---

## 1. Palette — neutral scale

| Token | Hex | oklch (as stored) |
|---|---|---|
| gray-50 | `#ffffff` | `oklch(1 0 0)` |
| gray-100 | `#f4f4f5` | `oklch(0.967 0.001 286.375)` |
| gray-200 | `#e8e8e8` | `oklch(0.93 0 0)` |
| gray-300 | `#cecece` | `oklch(0.85 0 0)` |
| gray-400 | `#9f9fa9` | `oklch(0.705 0.015 286.067)` |
| gray-500 | `#5c5c67` | `oklch(0.48 0.016 285.938)` |
| gray-600 | `#27272a` | `oklch(0.274 0.006 286.033)` |
| gray-700 | `#1b1b1b` | `oklch(0.22 0 0)` |
| gray-800 | `#131313` | `oklch(0.187 0 271.152)` |
| gray-900 | `#0f0f0f` | `oklch(0.168 0 0)` |
| gray-950 | `#09090b` | `oklch(0.141 0.005 285.823)` |

## 2. Palette — accent scale

| Token | Hex | oklch (as stored) |
|---|---|---|
| accent-50 | `#eff6ff` | `oklch(0.97 0.014 254.604)` |
| accent-100 | `#daecff` | `oklch(0.94 0.05 264)` |
| accent-200 | `#b0d4ff` | `oklch(0.87 0.11 264)` |
| accent-300 | `#7cadff` | `oklch(0.76 0.16 264)` |
| accent-400 | `#457eff` | `oklch(0.63 0.21 264)` |
| **accent-500** | `#1447e6` | `oklch(0.488 0.243 264.376)` |
| **accent-600** | `#193cb8` | `oklch(0.424 0.199 265.638)` |
| accent-700 | `#132d94` | `oklch(0.36 0.17 266)` |
| accent-800 | `#0d2272` | `oklch(0.3 0.14 266)` |
| accent-900 | `#091955` | `oklch(0.25 0.11 266)` |
| accent-950 | `#040f3e` | `oklch(0.2 0.09 266)` |

Components never reference these scales directly — only semantic tokens
(`bg-surface`, `text-muted-foreground`, …). The scales exist to be remixed by theme
presets.

## 3. Semantic tokens — light / dark

| Token | Tailwind class | Hex (light) | Hex (dark) | Defined as |
|---|---|---|---|---|
| `--background` | `bg-background` | `#ffffff` | `#131313` | gray-50 / gray-800 |
| `--sidebar` | `bg-sidebar` | `#ffffff` | `#0f0f0f` | `var(--background)` / `color-mix(in oklch, var(--background) 90%, black)` |
| `--surface` | `bg-surface` | `#ffffff` | `#1b1b1b` | gray-50 / gray-700 |
| `--surface-subtle` | `bg-surface-subtle` | transparent (`#000000`/0) | `#ffffff`/5 | **card fill** (2026-10-02) |
| `--surface-hover` | `bg-surface-hover` | `#e8e8e8` | `#1b1b1b` | gray-200 / gray-700 |
| `--muted` | `bg-muted` | `#f4f4f5` | `#27272a` | gray-100 / gray-600 |
| `--foreground` | `text-foreground` | `#09090b` | `#ffffff` | gray-950 / gray-50 |
| `--text-secondary` | — | `#5c5c67` | `#cecece` | gray-500 / gray-300 |
| `--muted-foreground` | `text-muted-foreground` | `#5c5c67` | `#9f9fa9` | gray-500 / gray-400 |
| `--primary` | `bg-primary` | `#1447e6` | `#193cb8` | accent-500 / accent-600 |
| `--primary-foreground` | `text-primary-foreground` | `#eff6ff` | `#eff6ff` | accent-50 |
| `--accent` | `bg-accent` | `#1447e6` | `#193cb8` | accent-500 / accent-600 |
| `--accent-foreground` | — | `#eff6ff` | `#eff6ff` | accent-50 |
| `--destructive` | `bg-destructive` | `#df0000` | `#df0000` | `oklch(0.55 0.25 27)` |
| `--destructive-foreground` | — | `#f5f5f5` | `#f5f5f5` | `oklch(0.97 0 0)` |
| `--success` | `bg-success` | `#13a147` | `#46b964` | `oklch(0.62 0.17 149)` / `oklch(0.7 0.16 149)` |
| `--warning` | `bg-warning` | `#dc8900` | `#f4a437` | `oklch(0.7 0.16 70)` / `oklch(0.78 0.15 70)` |
| `--info` | `bg-info` | `#0a8fd1` | `#44a8e7` | `oklch(0.62 0.14 240)` / `oklch(0.7 0.13 240)` |
| `--border` | `border-border` | `#000000` | `#ffffff` | pure black / white, **opacity applied by the caller** |
| `--input` | `bg-input` | `#cecece` | `#222222` | `oklch(85% 0 0)` / `oklch(25% 0 0)` |
| `--ring` | `ring-ring` | `#1447e6` | `#71717b` | accent-500 / `oklch(0.552 0.016 285.938)` |
| `--chart-1` | `bg-chart-1` | `#8ec5ff` | `#8ec5ff` | |
| `--chart-2` | `bg-chart-2` | `#2b7fff` | `#2b7fff` | |
| `--chart-3` | `bg-chart-3` | `#155dfc` | `#155dfc` | |
| `--chart-4` | `bg-chart-4` | `#1447e6` | `#1447e6` | accent-500 |
| `--chart-5` | `bg-chart-5` | `#193cb8` | `#193cb8` | accent-600 |
| `--chart-scale-01` | — | `#ebebeb` | `#313131` | `color-mix(in srgb, var(--border) 8%, transparent)` — the **empty** cell (level 0) |
| `--chart-scale-02` | — | `#b4c4f7` | `#152048` | `… accent 32% …` (level 1) |
| `--chart-scale-03` | — | `#7e9af1` | `#17296e` | `… 55% …` (level 2) |
| `--chart-scale-04` | — | `#4870eb` | `#183394` | `… 78% …` (level 3) |
| `--chart-scale-05` | — | `#1447e6` | `#193cb8` | `var(--accent)` (level 4) |
| `--chart-label` | `text-chart-label` | `#5c5c67` | `#9f9fa9` | `var(--muted-foreground)` |
| `--chart-grid` | — | `#e1e1e2` | `#2f2f2f` | `color-mix(in srgb, var(--foreground) 12%, var(--background))` |
| `--chart-tooltip-background` | `bg-chart-tooltip-background` | `#ffffff` | `#1b1b1b` | `var(--surface)` |
| `--chart-tooltip-foreground` | `text-chart-tooltip-foreground` | `#09090b` | `#ffffff` | `var(--foreground)` |
| `--chart-tooltip-muted` | `text-chart-tooltip-muted` | `#5c5c67` | `#9f9fa9` | `var(--muted-foreground)` |

**`--chart-*` (Bklit) are derived, never literals** (added 2026-10-06 with the heatmap
card). The registry ships literal greys; the 12 theme presets override `--accent` /
`--foreground` / `--surface` / `--muted-foreground` / `--border` inline on `<html>` and
**never** a `--chart-*` token, so a literal would freeze the chart on the default palette
on every preset — the same trap `--sidebar` documents. Derived, one definition in `:root`
covers light, dark and all 12 presets (hexes above are the **default** palette; they
retint per preset, e.g. Dracula's data ramp is `#d3b7d9 → #8a3d9a`). Locked by
`__tests__/index-css-chart-tokens-follow-theme.test.ts`.

Step 01 is **not** part of the data ramp. It is the level-0 slot — a day with zero
dictations — so it is the neutral `--border` at 10% (black in light, white in dark),
the same hairline every card border uses, rather than a weak tint of the colour that
means "a lot of dictations". Only steps 02 → 05 carry the accent.

Mixes use **sRGB, not oklch**: the ramp steps tint a chromatic token toward an
achromatic one, and oklch interpolates hue — an achromatic colour's hue reads as 0, so
`color-mix(in oklch, #1447e6 12%, #ffffff)` lands on **pink** `#fbe3ee` before sweeping
back to blue. The sRGB mix resolves that same step to `#e3e9fc`. Verified in Chrome.

Alpha tokens: `--accent-soft` = `#193cb8` at 10% (light) / 15% (dark);
`--accent-muted` = `#155dfc` at 40% (light) / 60% (dark).

Scrollbar: 14px track, thumb radius 10px with a 4px transparent border (6px visible).
Thumb `#cecece`, hover `#aeaeae` (light); `#3a3a3a`, hover `#555555` (dark).

## 4. Borders — what `/5`, `/8` and `/10` actually paint

The border token is pure black (light) / white (dark); the percentage is applied by
the utility. These are the resulting composite colours:

| Surface | `/5` | `/8` | `/10` | `/20` |
|---|---|---|---|---|
| light `surface` `#ffffff` | `#f2f2f2` | `#ebebeb` | `#e6e6e6` | `#cccccc` |
| light `surface-subtle` (transparent → composites as `#ffffff`) | `#f2f2f2` | `#ebebeb` | `#e6e6e6` | `#cccccc` |
| dark `surface` `#1b1b1b` | `#262626` | `#2d2d2d` | `#323232` | `#494949` |
| dark `surface-subtle` `#1f1f1f` | `#2a2a2a` | `#313131` | `#353535` | `#4c4c4c` |

House style (2026-10-02): **8%** for card/panel borders and dividers, **10%** for
standalone panels and control borders (buttons, toasts), **20%** for dashed "this is
a mock / inactive" frames. **5%** survives only as a control *fill*
(`bg-border/5`, hover `bg-border/8`) — it is no longer used for any border.

## 5. Contrast audit (measured, WCAG 2.1)

| Pair | Light | Dark |
|---|---|---|
| foreground on background | 19.90 | 18.58 |
| foreground on surface | 19.90 | 17.22 |
| muted-foreground on background | 6.60 | 7.08 |
| muted-foreground on surface-subtle | 6.00 | 7.31 |
| text-secondary on surface | 6.60 | 10.94 |
| primary on background | 6.83 | **2.11** |
| primary on surface | 6.83 | **1.95** |
| primary-foreground on primary | 6.28 | 8.11 |
| destructive on surface | 5.08 | 3.39 |
| success on surface | 3.38 | 6.88 |
| warning on surface | **2.76** | 8.38 |
| info on surface | 3.58 | 6.56 |

Two real gaps, flagged not fixed: **`primary` as text on a dark surface is 1.95:1**
and **`warning` on a light surface is 2.76:1** — both below the 3:1 non-text / 4.5:1
text floor. Fine for large graphics and icons, not for body copy.

## 6. Type

Family: **Inter Variable** (100–900, one face) + system sans fallback.
`--font-sans: "Inter Variable", sans-serif`; `--font-heading` = the same.

| px | rem | Class | Line-height (px) | Where it's used |
|---|---|---|---|---|
| 10 | 0.625 | `text-[0.625rem]` | ~14 | bubble timer, bubble error/blocked labels |
| 11 | 0.6875 | `text-[0.6875rem]` | ~16 | toggle-group options, dense chips |
| 12 | 0.75 | `text-xs` | 16 | captions, hints, bubble "Ready"/"Transcribing" |
| 13 | 0.8125 | `text-xs-plus` | 18 | dense UI: title-bar title, toggle-group tabs, dense banners |
| 14 | 0.875 | `text-sm` | 20 | **body default**, setting-row labels, buttons |
| 16 | 1 | `text-base` | 24 | inputs, page copy |
| 18 | 1.125 | `text-lg` | 28 | section headings (h2) |
| 20 | 1.25 | `text-xl` | 28 | page headings |
| 24 | 1.5 | `text-2xl` | 32 | display |

Weights: 400 body · 500 labels/controls/buttons and every h1/h2 heading · 600
inline emphasis, dense uppercase labels, badges, stat values and card-level h3/h4 · 700 emphasis.
Tracking: normal `0`; `tracking-wider` `0.05em` on dense uppercase-ish chips;
`tight` `-0.025em` / `-0.01em` on large headings.

## 7. Spacing

Base unit **4px** (`--spacing: 0.25rem`).

| px | Class | rem |
|---|---|---|
| 2 | `0.5` | 0.125 |
| 4 | `1` | 0.25 |
| 6 | `1.5` | 0.375 |
| 8 | `2` | 0.5 |
| 10 | `2.5` | 0.625 |
| 12 | `3` | 0.75 |
| 14 | `3.5` | 0.875 |
| 16 | `4` | 1 |
| 20 | `5` | 1.25 |
| 24 | `6` | 1.5 |
| 32 | `8` | 2 |
| 40 | `10` | 2.5 |
| 48 | `12` | 3 |
| 64 | `16` | 4 |

## 8. Radius

`--radius: 0.625rem` → **10px**, mapped to `rounded-lg`.

| Class | px | rem |
|---|---|---|
| `rounded-xs` | 2 | 0.125 |
| `rounded-sm` | 4 | 0.25 |
| `rounded-md` | 6 | 0.375 |
| **`rounded-lg`** | **10** | **0.625** |
| `rounded-xl` | 12 | 0.75 |
| `rounded-2xl` | 16 | 1 |
| `rounded-full` | 9999 (circle/pill) | — |

Rule: every control and panel uses `rounded-lg` (10px). Pills, switches, radios,
avatars, icon discs use `rounded-full`. Checkbox keeps `rounded-sm`.

## 9. Elevation

| Token | Value |
|---|---|
| `shadow-xs` | `0 1px 2px 0 rgb(0 0 0 / 0.05)` |
| `shadow-sm` | `0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)` |
| `shadow-md` | `0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)` |
| `shadow-lg` | `0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)` |
| `shadow-xl` | `0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)` |
| `shadow-2xl` | `0 25px 50px -12px rgb(0 0 0 / 0.25)` |

Settings cards use `shadow-xs`-level separation via an 8% border instead of a real
shadow; dialogs and popovers use `shadow-md`/`lg`.

## 10. Focus & state

- Baseline (every element): `outline: 2px solid var(--ring)` (`#1447e6` light,
  `#71717b` dark), `outline-offset: 2px`.
- Components override with `focus-visible:ring-1` (1px) + `ring-ring`, `outline-hidden`.
- Hover: `hover:bg-surface-hover` on ghost controls, `hover:bg-muted` on quiet ones.
- Disabled: `opacity-50`, `cursor-not-allowed`, `pointer-events-none`.
- Active/pressed: `active:translate-y-px` (1px) on buttons.

## 11. Motion

| Animation | Duration | Easing |
|---|---|---|
| `fadeInUp` (page mount) | 220ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| `fadeIn` | 180ms | `ease-out` |
| `scaleIn` | 160ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| `slideUp` | 220ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| `bubbleEnter` | 180ms | `cubic-bezier(0.16, 1, 0.3, 1)` |
| `bubbleExit` | 200ms | `cubic-bezier(0.5, 0, 0.5, 1)` |
| `bubbleShimmerSlide` | 2.2s | `linear`, infinite |
| `bubbleDotBlink` | 1.2s | `ease-in-out`, infinite |
| `pulseRing` | 1.8s | `cubic-bezier(0.4, 0, 0.6, 1)`, infinite |
| `glowPulse` | 2.5s | `ease-in-out`, infinite |
| control transitions | 150ms (colour) / 200ms (indicator slide) | `ease-out` |

`prefers-reduced-motion: reduce` clamps animations to `0.01ms / 1 iteration` and
kills the glow, pulse and blink loops.

## 12. Component metrics

**Button** — 14px / weight 500 / line-height 1.3 (18.2px) / **radius 8px**
(`rounded-lg`).

| | Light | Dark | Class |
|---|---|---|---|
| fill | `#000000`/5 → `#f2f2f2` | `#ffffff`/5 → `#2a2a2a` on a card | `bg-border/5` |
| hover | `#000000`/8 → `#ebebeb` | `#ffffff`/8 → `#313131` | `hover:bg-border/8` |
| border | `#000000`/8 → `#ebebeb` | `#ffffff`/10 → `#353535` | `border-border/8 dark:border-border/10` |

The **accent blue** (`default`) variant is the one button with **no border**
(`border-transparent`) and keeps `bg-primary #1447e6` / `#193cb8`. `destructive` and
`warning` keep their tints (base border still applies); `ghost` and `link` opt out of
both fill and border.

| Size | Padding | Gap | Box | Icon |
|---|---|---|---|---|
| `xs` | 10×4px | 4px | auto | 12px |
| `sm` | 12×6px | 4px | auto | 16px |
| default | 12×6px | 8px | auto | 16px |
| `lg` | 16×8px | 8px | auto | 16px |
| `icon-xs` | — | — | 24×24 | 12px |
| `icon-sm` | — | — | 32×32 | 16px |
| `icon` | — | — | 36×36 | 16px |
| `icon-lg` | — | — | 40×40 | 16px |

**Input** — 32px tall (`h-8`), 12×8px padding, radius 10px, `bg-input/50`, text 16px
(14px at ≥768px).

**Switch** — default 44×20px (2px border), thumb 24×16px, radius full, checked
`bg-primary` / border `primary/30`, unchecked `bg-input`. Small: 28×16px, thumb 16×12px.

**ToggleGroup** (segmented control) — track: radius full, `bg-border/10`
(`#000000`/10 → `#e6e6e6` light; `#FFFFFF`/10 → `#353535` over a dark card),
3px padding, 1px border at 8%. Options: radius full, 8×4px padding, 11px text,
`tracking-wider`.
Selected: `bg-surface` + `shadow-xs` + `text-foreground` (neutral, not accent-filled);
indicator slides 200ms `ease-out`. `sm` variant: radius 10px, 2px track padding,
10×4px option padding.

**SettingRow** — 16×8px padding, 24px gap between label and control, label 14px/500.

**Sidebar rail** — `bg-sidebar` (`--sidebar`). Dark: `#0f0f0f` (gray-900), one step
below the gray-800 `#131313` canvas so the rounded content column reads as raised on
it. Light: identical to the canvas (`#ffffff`) — light surfaces separate with borders,
not fills. The token is a `color-mix` of `--background` so every theme preset keeps the
same relationship (amoled stays `#000000`, nord lands at L 0.162).

**Sidebar nav link** — radius 10px, full-width, 14px text, 8px horizontal padding
(icon column pinned at 16px from the rail edge in both collapsed and expanded
states). Border: **0% when inactive** (`#FFFFFF`/0 — the 1px box is still reserved so
nothing shifts) and **10% when active** (`border-border/10` = `#000000`/10 light,
`#FFFFFF`/10 dark) with the card surface `bg-surface` and `text-foreground`/500.

**SettingsSection card** — radius 10px, 1px border at 8%, `bg-surface-subtle`,
children divided by an 8% hairline; section wrapper gap 16px; heading 18px/500.
Rows carry their own 16×8px padding, so the card itself adds none (adding `p-4` on
top would double the inset to 32px).

**Cards (general)** — `rounded-lg border border-border/8 bg-surface-subtle p-4`:

| | Light | Dark |
|---|---|---|
| fill | `#000000`/0 → canvas shows through (`#ffffff`) | `#ffffff`/5 → `#1f1f1f` on the `#131313` canvas |
| border | `#000000`/8 → `#ebebeb` | `#ffffff`/8 → `#313131` |

Panels that must stay opaque (floating bulk bar, sticky list header, popover) do
**not** use the card token — they use `bg-surface` / `bg-surface/95`.

**Load-failure card** (`EmptyState variant="error"`, and the canonical
`ConnectionStatusScreen` recovery card) — `rounded-lg border border-border/8
bg-surface px-24 py-40`, children centered with a 16px gap. Icon: 40px glyph on
a 64px `bg-destructive/10` disc (`text-destructive`). Title 18px `text-foreground`
(600 on `EmptyState`'s h3, 500 on `ConnectionStatusScreen`'s h2);
description 14px `text-muted-foreground`, capped at 512px;
CTA is the accent-blue Button with the 16px reload glyph. The destructive
accent is the disc only — never a card-wide red fill.

**Dialog / AlertDialog** — radius 10px, **padding 16px** (`p-4`), 1px ring at 8%
(`ring-border/8`):

| | Light | Dark |
|---|---|---|
| fill | `#ffffff` (gray-50, `bg-surface`) | `#1b1b1b` (gray-700, `bg-surface`) |
| border | `#000000`/8 → `#ebebeb` | `#ffffff`/8 → `#2d2d2d` |

**Data pages (incl. Settings)** — max width 896px, 64px side padding, 80px top,
24px bottom, 24px gap between stacked blocks. That is the one page shell, used
verbatim by every data page and every page-level state screen:
`mx-auto flex min-h-full w-full max-w-4xl flex-col gap-6 px-16 pt-20 pb-6`
(state screens append `items-center justify-center`). Settings adds a heading
tier of its own: 16px from the heading to the first card (`gap-4`), then its
section cards stack at **48px** (`gap-12`) — deliberately looser than the 24px
the shell gives a data page's own stacked blocks.

**Bubble pill** (overlay) — radius full, 16×10px padding, 12px gap, 1px border at 8%,
`bg-surface`. Settings preview variant: 16×6px padding.
Bubble icon buttons 24×24px, 4px inline-start margin. Recording dot 6px.
Visualizer bars 3px wide, 3px gap, 5–22px tall. Transcribing dots 4px.

## 13. Layout

Breakpoints (Tailwind default): sm 640 · **md 768** · lg 1024 · xl 1280 · 2xl 1536.
Z-index: base content `z-0`/`z-10`, sticky chrome `z-20`, overlays/dialogs `z-50`,
top-most `z-100`.

## 14. Theme presets

`default`, `amoled`, `ayu`, `catppuccin`, `custom`, `dracula`, `github`, `monokai`,
`nord`, `sepia`, `solarized`, `tokyo-night` (`renderer/src/themes/`).
Presets override **semantic tokens only** — never the gray/accent scales — so every
component follows the active scheme without edits. Values in this document are the
base (`default`) scheme.
