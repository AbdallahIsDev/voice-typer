# Keyboard Shortcuts Render as Keycap Chips — Never a `+` Separator

**Status**: Decided (2026-08-22, refined 2026-08-24)
**Decision owner**: voice-typer UX
**Supersedes**: the previous `formatHotkey(...)` plain-text rendering that
joined keys with `+`
**Related code**:
- `voice_typer/client/src/renderer/src/components/common/Kbd.tsx` — the single keycap source of truth (`Kbd`, `KbdGroup`)
- `voice_typer/client/src/renderer/src/components/hotkey/HotkeyChips.tsx` — the shared shortcut renderer (splits catalog `keys` into adjacent chips)
- `voice_typer/client/src/renderer/src/components/ui/__tests__/focus-ring-contrast.test.tsx` — sibling contrast contract for interactive primitives
- Consumers: sidebar nav tooltips/chips, TitleBar tooltips, Help overlay, Home's hint line, Settings `HotkeyPicker`, Diagnostics, onboarding

## Context

Keyboard shortcuts were historically rendered by formatting the catalog's
shortcut string into plain text: `Ctrl+H`, `Ctrl+Alt+V`. The `+` is a
display artifact, not part of the shortcut — but rendered as text it reads as
"Ctrl plus H is a plus key", competes with real key symbols, and invites
drift (some surfaces trimmed spaces, some used different separators). A
parallel shadcn `ui/kbd` component also existed and had drifted in font and
contrast tokens from the app's own keycap component.

## Decision

Every shortcut display renders through the shared `HotkeyChips` component,
which renders each key of a multi-key shortcut as a separate `Kbd` keycap
chip, adjacent to the next chip with only a small, consistent visual gap
(`KbdGroup`'s `gap-1`). **No `+` (or any punctuation) appears between keys.**

- `components/common/Kbd.tsx` is the **single source of truth** for keycap
  presentation app-wide; the shadcn `ui/kbd` file was removed. `HotkeyChips`
  imports from it; no surface duplicates keycap markup.
- `<Kbd as="code">` is used for voice-inserted punctuation characters.
- macOS glyph output ("⌃B", "⌘⇧V") is exempt — modifiers there are
  conventionally joined without separators.
- The underlying shortcut strings, catalog `keys`, and registration behavior
  are unchanged; `aria-keyshortcuts` keeps exposing the actual binding to
  assistive technology. The change is strictly presentation.

## Why

- **The `+` lies.** `Ctrl+H` reads like an expression to evaluate; adjacent
  keycaps read like "press these keys in order", which is what shortcuts are.
- **One renderer, zero drift.** Before the consolidation, the sidebar, Help
  overlay, Settings picker, and cheat sheet each rendered shortcuts slightly
  differently (spacing, font, contrast). A single component means a styling
  fix lands everywhere at once.
- **Theme contrast is owned in one place.** `Kbd` carries the dark-modal and
  tooltip-content contrast treatments (foreground-based tokens, never
  `text-background`/`bg-background/N` which render dark-on-dark in dark
  mode). Centralizing means a new tooltip surface cannot silently ship an
  invisible keycap.

## Alternatives considered

- **Keep plain text but standardize the separator.** Rejected: any separator
  still reads as a key; only chip adjacency communicates "separate keys".
- **Use `+` only for macOS.** Unnecessary — macOS glyphs already join
  modifiers by convention; a third rendering mode adds drift surface.
- **Per-surface custom keycap markup.** Rejected: this is exactly the drift
  the unification removed (two parallel `Kbd` components had already diverged
  in font and tokens).

## User impact

- Shortcuts read as clean adjacent keycaps everywhere: sidebar tooltips,
  TitleBar tooltips, the Help overlay, Home's "Press … or click" hint, the
  Settings hotkey picker, Diagnostics, and onboarding.
- Dark-mode and tooltip contexts keep readable keycaps because contrast lives
  in the one shared component.
- Nothing changes functionally — the same shortcuts are registered and
  exposed to assistive tech via `aria-keyshortcuts`.

## Test coverage

The presentation contract is pinned by the component suites (Sidebar, Title
Bar, Help overlay) which assert `HotkeyChips`/`Kbd` usage and the absence of
`+`-joined plain-text shortcut labels; the accessibility contract
(`aria-keyshortcuts` always present) is asserted in the same suites.
