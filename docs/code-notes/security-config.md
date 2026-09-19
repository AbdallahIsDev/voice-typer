# Security & config comment notes

Relocated essays from inline comment cleanup. Pointers from code:
`# NOTE: see docs/code-notes/security-config.md#<anchor>`.

## ipc-set-config-allowlist

`IPC_CONFIG_ALLOWLIST` (`config_validators/allowlist.py`) is the
SEC-002 contract: only map keys are mutable via IPC `set_config`.
Anything not listed is dropped. Excluded on purpose:
`schema_version` (migration), `wayland_warned` (UX state),
`qwen_model_path` / `parakeet_model_path` / `corrections_path`
(trusted paths set by download/file-picker flows).

When adding a field, also extend
`tests/test_server.py::TestDispatchSetConfigAllowlist`.

## shared-validation-bounds

`MAX_RECORDING_TIME_SECONDS_*` and `STREAMING_*_SECONDS_MIN` live in
`config_validators/allowlist.py` (import-safe leaf). Both the IPC
validator and `Config._coerce_*` must read these constants — dual
sources previously let IPC accept values the load path then silently
clamped.

## noise-suppression-enum

Canonical set is `NOISE_SUPPRESSION_METHODS` in
`config_validators/allowlist.py` (currently
`{"rnnoise", "gtcrn", "none"}`). IPC validator, Config schema, and
`audio_filters/noise_suppressor.py` must all import this constant.
Do not re-inline literals.

## redaction-patterns

`security/redaction.py`:

- Order: labeled/`Bearer`/`Token`/`sk-`/`gsk_` first, generic
  `>=20` alnum last (threshold aligned with `_MIN_REDACT_LEN`).
- Path flanks (`/`, `\\`) prevent false positives on long path
  components.
- `sha256=` / `thread=` shields keep public digests and thread names
  out of the catch-all; hash-shaped values behind `thread=` still
  redact (fail closed).
- Env-var **names** are whitelisted (`_PUBLIC_ENV_VAR_NAMES`); values
  still redact. No syntactic name-vs-value discriminator exists.
- Flag / `key=value` patterns: `--`/`=` delimiter must sit outside
  the keyword alternation; `=` stays inside capture group 1 so
  output is `password=***`.

## model-integrity-cache

`security/model_integrity.py` caches SHA-256 by
`(repo_id, relpath, st_mtime_ns, st_size)`. Cache hit skips re-hash
of unchanged multi-GB weights; it does not weaken the pinned-manifest
check. Local models (`revision: "local"`) hard-fail when `files` is
empty — there is no upstream commit pin.

## url-allowlist-ssrf

`security/url_allowlist.py`: HTTPS required for non-loopback; IP
literals checked against private/link-local ranges; best-effort DNS
rebinding check after allowlist+HTTPS. `extend_url_allowlist` is
user/self-host driven (C-DATA-1 allowed categories), not telemetry.

## config-save-acl

`config/_saving.py`: Windows owner-only ACL via `icacls` list-form
(no shell). Dir ACL tightened only on a directory this process just
created (open-file ACL rewrite is unsafe on older Python). Per-file
enforcement still runs; failed paths are recorded for tray notify.

## config-preset-handlers

`config_applier.py`: side-effect handlers are a registered list, not
an if-chain. Order matters: audio-preset mutates Config before
filter-chain reads it. Auto-switch `audio_preset` → `"custom"` when
IPC writes individual filter keys that a named preset would overwrite.
