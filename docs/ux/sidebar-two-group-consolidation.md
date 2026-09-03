# Sidebar Navigation — Two-Group Consolidation

**Status**: Decided (2026-08-25, user product decision)
**Decision owner**: voice-typer UX
**Supersedes**: the earlier three-group hierarchy (Main / Power features / System)
**Related code**:
- `voice_typer/client/src/renderer/src/components/layout/Sidebar.tsx` — nav group definitions (`nav.group.main`, `nav.group.system`, `pinnedToBottom`)
- `voice_typer/client/src/renderer/src/index.css` — `[dir="rtl"] .nav-directional-icon`
- `voice_typer/client/src/renderer/src/components/layout/__tests__/Sidebar.test.tsx` — group structure + `mt-auto` pinning tests

## Context

The sidebar previously organized its destinations into three labeled groups:

1. **Main** — Home, History, Analytics
2. **Power features** — Templates, Vocabulary, Models, Microphone
3. **System** — Settings, About & Privacy

In practice the split produced a two-item delta between the first two groups
(3 items vs. 4 items), and the "Power features" heading added no information —
every destination in the sidebar is a feature; the heading only inserted
vertical space and a second visual anchor the eye had to traverse.

## Decision

The sidebar has exactly **two groups**:

1. A **header-less top group** (visually unlabeled; `aria-label="Main"`
   preserved for assistive technology) containing Home, History, Analytics,
   Models, Templates, Vocabulary — the destinations a user touches daily and
   weekly.
2. The **System group** (visible heading, pinned to the sidebar's bottom edge
   via `mt-auto`) containing Settings, Microphone, About & Privacy. Microphone
   moved here deliberately: input-device configuration is a set-and-forget
   setting, not a daily destination.

The `nav.group.power` i18n key was removed; do not reintroduce a third group
or a "Power features" heading.

## Why

- **Heading budget vs. information gain.** A heading earns its vertical space
  when it separates clusters that users mentally treat differently. "Main"
  vs. "Power features" did not survive that test — the distinction is
  frequency, not kind, and frequency is already encoded by position.
- **Position encodes priority.** Frequently used destinations sit at the top;
  low-traffic informational/system destinations cluster at the bottom. This
  keeps the mental model stable across window sizes and both sidebar states
  (expanded / collapsed rail).
- **Layout structure, not spacers.** The System group is pinned with `mt-auto`
  (flex auto margin) inside the `min-h-full flex-col` nav, so on short windows
  the auto margin collapses to 0 and the nav scrolls instead of overflowing.

## Alternatives considered

- **Keep three groups.** Rejected: the heading's information gain did not
  justify the added height and the awkward 3/4-item split.
- **Single flat list.** Rejected: Settings/Microphone/About & Privacy would
  interleave with daily destinations; the bottom cluster also loses its
  bottom-pinned anchor.
- **Reorder items within three groups.** Rejected: does not address the
  heading overhead; reshuffling order without changing group structure
  breaks discoverability for existing users.

## User impact

- Daily destinations (Home, History, Analytics, Models, Templates,
  Vocabulary) are reachable with less scrolling and one fewer visual anchor.
- System destinations stay in a stable, bottom-pinned cluster — muscle memory
  for "Settings is at the bottom" keeps working in both sidebar states.
- Screen-reader users get the same hierarchy (`aria-label="Main"` retained;
  visible-heading removal is presentation-only).

## Test coverage

`Sidebar.test.tsx` pins the structure: the System group's section carries
`mt-auto`, the Main group's sections do not, and the group item assignments
match the list above. A future edit that re-adds a third group or reorders
items fails these tests.
