# Microphone Enumeration — One Canonical Host-API View

**Status**: Decided (2026-08-25, refined with the System Default semantics)
**Decision owner**: voice-typer UX / audio
**Supersedes**: the previous raw PortAudio enumeration shown in the UI
(duplicate devices per host API)
**Related code**:
- `voice_typer/server/server_platform/microphone_list.py` — `list_microphones()`, `_canonicalize_host_apis()`, `_match_canonical_mic_by_name()`
- `voice_typer/server/server_platform/remote_session.py` — `_is_invalid_device_name()` (source-level filtering)
- `voice_typer/server/server_platform/__init__.py` — re-export surface
- Consumers: tray submenu (`tray.set_microphones`), IPC `get_microphones`, Microphone page, onboarding

## Context

PortAudio exposes every physical endpoint **once per host API**. On Windows a
single USB headset typically appears four times (MME, DirectSound, WASAPI,
WDM-KS); on other platforms similar multi-view duplication exists. The
original UI listed the raw enumeration, so users saw 17 records representing
3 real devices — and "which one do I pick?" was unanswerable, since any of
the duplicates opens the same hardware. Worse, MME truncates device names at
31 characters, making same-device records *look* like different devices with
mangled names.

## Decision

1. **`list_microphones()` returns the canonical host-API view**: Windows →
   WASAPI, macOS → Core Audio, Linux → PulseAudio when present. When the
   preferred host API enumerates zero devices, enumeration falls back
   gracefully to the unfiltered list so the app stays usable.
2. **Invalid devices are filtered at the enumeration source**
   (`_is_invalid_device_name`: placeholder endpoints like `Input ()`,
   empty/whitespace names, generic-label-only entries) — not by UI-only
   filters — so every consumer (tray, recorder, tests, renderer) sees the
   same clean set.
3. **Same-name records *within* the canonical view are genuinely distinct
   devices** and stay distinct (`#N` suffix ids) — they are never merged by
   display name alone.
4. **`System Default` is a separate selection semantic, not a device**: it
   follows the OS default dynamically, is never deduplicated against the
   physical device it currently resolves to, and is never dropped from any
   microphone surface. It is always listed first.
5. **One enumeration path**: every consumer reads the canonical model
   (`list_microphones` → `app._microphones` → IPC `get_microphones` /
   `tray.set_microphones`); the tray is a pure pass-through. No consumer calls
   `sd.query_devices()` directly.

## Why

- **The canonical view IS the device-identity strategy.** No stable unique id
  exists across host APIs; showing one host API's view is both what the OS's
  own Settings input list shows and what makes ids (`"<hostapi>|<name>[#N]"`)
  stable across reboots and hot-plugs.
- **Source-level filtering is consumer-proof.** A renderer-only filter leaves
  bogus devices selectable via the tray and reappears wherever a new consumer
  enumerates devices.
- **Deduplicating by name would hide real hardware.** Two physical mics can
  share a display name; name-based merging silently removes one. Within one
  host API, duplicates are real.
- **`System Default` is a semantic, not a device.** Collapsing it into
  whichever physical mic currently holds the OS default would freeze the
  selection to that device and break the follow-the-OS-default behavior.

## Alternatives considered

- **Show all host-API records with an API badge.** Rejected: 4x list noise
  for zero capability gain; users still can't tell views apart.
- **Deduplicate by display name across host APIs.** Rejected: hides genuine
  same-name devices and breaks stable ids.
- **Hide virtual devices (VB-Cable, AudioRelay, WO Mic…).** Rejected: they
  are legitimate inputs (routing, virtual conferencing) and the OS Settings
  input list shows them; the canonical view must equal the OS list 1:1.
- **Render-side filtering of invalid names.** Rejected (see Why).

## User impact

- The Microphone page, tray submenu, and onboarding show the same short list
  the OS Settings shows: one record per physical input, plus `System Default`
  first.
- Selections persist and survive reboots/hot-plugs via stable device ids; a
  genuinely-unavailable selection recovers silently to System Default
  (one diagnostic log line — no warning dialogs).
- Virtual/USB/Bluetooth devices keep working; nothing legitimate is hidden.

## Test coverage

The microphone suites pin: the canonical host-API selection per platform and
its zero-device fallback, invalid-name filtering at the source, `#N` suffix
preservation for same-name devices, `System Default` first and
non-deduplicated, and the single-enumeration-path rule (tray is a
pass-through of `app._microphones`).
