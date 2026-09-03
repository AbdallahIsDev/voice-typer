# Models Page — No-Model State Is a Dismissible Banner, Not a Centered Empty State

**Status**: Decided (2026-08-30)
**Decision owner**: voice-typer UX
**Supersedes**: the previous centered `EmptyState` treatment of the
no-model state
**Related code**:
- `voice_typer/client/src/renderer/src/pages/Models.tsx` — banner render + `models:noModelBannerDismissed` sessionStorage logic (`data-testid="models-no-model-banner"`)
- `voice_typer/client/src/renderer/src/pages/__tests__/ModelsPage.test.tsx` — banner presence, copy, dismissal, and session-scoped persistence
- `voice_typer/client/src/renderer/src/i18n/translations/*.json` — `models.noModelBanner` (all 8 locales)

## Context

When no speech model is selected (`config.model_size === ""`, the backend's
`NO_MODEL_SIZE` sentinel), the Models page previously rendered the standard
`EmptyState` component: a centered icon/title/description block with vertical
padding that consumed roughly 120px above the fold. Because the Models page
is exactly where the user goes to **fix** the empty state, a centered block
pushed the model cards — the actionable content — below the fold.

## Decision

The no-model state renders as a **compact, dismissible banner in the normal
page flow** (between the active-model summary and the Local/Cloud tab
switcher):

- Standard design-system surface: `rounded-xl border border-border/10
  bg-(--bg-subtle)` with muted icon + primary text — adapts to every theme
  (light/dark/custom presets) via CSS variables, not hardcoded tints.
- Copy names the actual state and the fix (unambiguous per the app's
  copy-standard): **"No speech model is selected. Select a model below."**
  (`models.noModelBanner` key) — not the vague "No models are available".
- `role="status" aria-live="polite"` so screen readers announce it without
  interrupting.
- A close `X` button writes `sessionStorage["models:noModelBannerDismissed"]
  = "1"` — **session-scoped**: the dismissal survives in-app navigation and
  reloads within the running app, and is cleared only when a model is
  actually selected (or when the app closes, discarding sessionStorage).
  The flag's reset effect is guarded on `config?.model_size` truthiness so a
  still-loading config cannot clear the flag on mount.

## Why

- **The fix is below the banner.** On the Models page the empty state and the
  remedy are the same screen; the banner shape keeps both visible and keeps
  vertical space for the model cards.
- **Dismissible-but-recurrent is the right persistence.** "No model selected"
  is a real, unresolved state — auto-hiding it forever after one dismiss
  would leave users on a silently non-dictating app. Session scoping means:
  dismiss once per session (it stops nagging during that session's browsing),
  but a fresh app launch with still-no-model shows it again.
- **Flow placement beats overlay.** A sticky/floating overlay read as a
  disconnected layer; a centered empty block competed with the content the
  user is there for.

## Alternatives considered

- **Centered `EmptyState` (status quo).** Rejected: ~120px of vertical space
  pushed actionable model cards below the fold.
- **Permanent banner (non-dismissible).** Rejected: nags users who are
  mid-exploration; dismissal with session re-surfacing achieves the same
  safety without the noise.
- **Toast/snackbar.** Rejected: transient — the condition persists, so the
  indicator must persist too.
- **Sticky overlay.** Rejected: floats detached from the layout and overlaps
  content while scrolling.

## User impact

- New installs (no model selected) see a one-line, theme-correct prompt with
  the model cards immediately reachable below it.
- Dismissing is respected for the whole session; selecting a model clears the
  banner and its flag for good.
- Screen readers get a polite, specific announcement; the banner carries a
  stable `data-testid` for automation.

## Test coverage

`ModelsPage.test.tsx` asserts: the banner renders with the exact
`models.noModelBanner` copy when `model_size === ""`; the close button sets
`sessionStorage["models:noModelBannerDismissed"] = "1"`; and a still-loading
config does not clear the dismissed flag on mount.
