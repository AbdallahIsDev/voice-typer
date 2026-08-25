# ADR-S1-CR-67: Migration away from `_RecordingModule` sys.modules hack

## Status

<Accepted — COMPLETE for the recording package (2026-08-25). The
`_RecordingModule` custom module class, the `_MUTABLE_*` frozensets,
and every package-namespace patch site have been removed;
production readers (`recorder.py`, `_recorder_split.py`) import
`voice_typer.server.recording.resampling` at call time and tests patch
submodules directly. `prewarm/__init__.py` and `server_platform/__init__.py`
keep only the milder `_pkg.X` call-time indirection — no custom module
classes remain anywhere in the codebase.>

## Context

`voice_typer/server/recording/__init__.py` (lines 348-475) installs a custom
module subclass `_RecordingModule(sys.modules[__name__].__class__)` whose
`__getattr__` / `__setattr__` overrides route reads/writes of a small set
of mutable globals through to the owning submodule:

- `_MUTABLE_RESAMPLING = frozenset({"_resample_poly", "_resample_poly_error",
  "_resample_poly_error_time", "_scipy_preloader_thread"})` → routed to
  `voice_typer.server.recording.resampling`.
- `_MUTABLE_BUFFER = frozenset({"_buffer_clear_worker"})` → routed to
  `voice_typer.server.recording.buffer`.

This custom module class exists **only** to preserve test-patch compatibility
during the Phase 4.5 god-class decomposition: tests historically did
`monkeypatch.setattr("voice_typer.server.recording._resample_poly_error", ...)`
or `rec_mod._resample_poly_error = ...` and expected the write to land on
`resampling.__dict__` (where production code reads it via `global`) — but a
plain module's `__dict__` snapshot wouldn't propagate the write.

`_RecordingModule` is ~50 LOC of `__init__.py` boilerplate whose sole purpose
is to keep these tests passing.  Once every test site has been migrated to
patch the owning submodule directly (e.g.
`monkeypatch.setattr(resampling, "_resample_poly_error", ...)`),
`_RecordingModule` and the `_MUTABLE_*` frozensets can be deleted — shrinking
`recording/__init__.py` and removing a non-obvious metaprogramming pattern
that future contributors must understand (E3 — no spaghetti; E13 — no
band-aids).

**Note on review.md scope drift:** review.md entry #4 (S1-CR-67) also
mentions `_PrewarmModule` and `_ServerPlatformModule`.  Investigation during
Wave 1 (rg `class _(Prewarm|ServerPlatform)Module`) confirms **neither
exists** in the current codebase — `prewarm/__init__.py` and
`server_platform/__init__.py` have only plain `import` re-exports (no custom
module subclass).  The migration is therefore scoped to `_RecordingModule`
only.

## Decision

Chip-away migration (per E16 — partial progress acceptable): migrate test
sites that read/write `_MUTABLE_*` names through the `voice_typer.server.
recording.X` package namespace to instead target the owning submodule
directly (`voice_typer.server.recording.resampling.X` or
`voice_typer.server.recording.buffer.X`).

**Why this works without breaking production code (this wave):**
`_RecordingModule.__getattr__` and `__setattr__` route the `_MUTABLE_*`
names to the submodule at call time — so they are *indifferent* to whether
the test wrote to the package namespace or directly to the submodule.  A
test that patches `resaming._resample_poly_error` (directly) and a test
that patches `recording._resample_poly_error` (via `_RecordingModule`'s
`__setattr__` routing) both end up writing the same `resampling.__dict__`
slot — and production code (which reads via `global` in `resampling.py`)
sees the patched value either way.  This means each test site can be
migrated independently without breaking others; no flag-day cutover is
required.

**Removal criterion:** `_RecordingModule`, `_MUTABLE_RESAMPLING`, and
`_MUTABLE_BUFFER` may be deleted once the count of remaining sites (below,
§3) reaches 0.  Until then, they MUST remain in place (E15 — no premature
removal; E14 — no regressions).

## Consequences

### Positive
- Each migrated test site is one step closer to deleting `_RecordingModule`
  (~50 LOC reduction in `recording/__init__.py`).
- Migrated tests no longer depend on the metaprogramming hack — they read
  more straightforwardly (`resampling._resample_poly_error` instead of
  `recording._resample_poly_error`), reducing the "magic" surface for new
  contributors.
- Each migration is atomic and low-risk (see "Why this works" above) — no
  flag-day cutover required.

### Negative
- Multi-wave effort: requires touching every test file that patches a
  `_MUTABLE_*` name.  Tracked in §3 below.
- The non-`_MUTABLE` package-namespace patches (e.g.
  `voice_typer.server.recording._get_resample_poly`,
  `voice_typer.server.recording._secure_clear_array_background`) cannot be
  migrated in isolation — production code reads those via
  `_recording_pkg.X` (not via local globals), so changing only the test
  side would break the patch.  These are a separate cleanup item that
  requires coordinated production-code changes (out of scope for the
  `_RecordingModule` removal itself; tracked in §3 as "Related but
  separate").

### Neutral
- `prewarm/__init__.py` and `server_platform/__init__.py` re-export many
  names (stdlib modules, submodule functions) so existing patches of the
  form `voice_typer.server.{prewarm,server_platform}.X` keep working.  This
  is unrelated to `_RecordingModule` (no custom class is installed on
  those packages) — it is plain re-export boilerplate.  Future cleanup
  could shrink it, but it is not blocking the `_RecordingModule` removal.

---

## Section 1 — Migration Plan

1. For each test file with `monkeypatch.setattr(...)` or direct attribute
   writes targeting `voice_typer.server.recording.X` where `X` is one of
   the `_MUTABLE_*` names, replace the package-namespace target with the
   owning submodule: `voice_typer.server.recording.resampling.X` (for
   `_MUTABLE_RESAMPLING` names) or `voice_typer.server.recording.buffer.X`
   (for `_MUTABLE_BUFFER` names).
2. Add a local `from voice_typer.server.recording import resampling` (or
   `buffer`) import in the test method (or at the top of the test file if
   used in many tests).
3. Run the migrated test file (`python -m pytest tests/<file>.py -x -q
   --no-cov`) to verify no regressions (E6 / E14).
4. Repeat until no `_MUTABLE_*` patches remain in `tests/`.
5. Final step (separate future wave): delete `_RecordingModule`,
   `_MUTABLE_RESAMPLING`, `_MUTABLE_BUFFER`, and the
   `sys.modules[__name__].__class__ = _RecordingModule` line from
   `recording/__init__.py`.  Run the full test suite to confirm.

---

## Section 2 — Completed This Wave (W1-A8)

**Date:** 2026-08-22 (Implementation Wave 1, Sub-Agent #8)

### File: `tests/test_recording.py` — 13 sites migrated (4 test methods)

| Test method | Lines (post-edit) | Names migrated |
|---|---|---|
| `TestResampleRetry.test_resample_retry_after_timeout` | 730-759 | `rec_mod._resample_poly_error` → `resampling._resample_poly_error`; `rec_mod._resample_poly_error_time` → `resampling._resample_poly_error_time`; `rec_mod._RESAMPLE_RETRY_INTERVAL` → `resampling._RESAMPLE_RETRY_INTERVAL`; `rec_mod._resample_poly_error` (read) → `resampling._resample_poly_error` |
| `TestResampleRetry.test_resample_not_retried_before_timeout` | 761-778 | Same names — `rec_mod._resample_poly_error` (write + read) and `rec_mod._resample_poly_error_time` |
| `TestScipyPreloader.test_start_scipy_preloader_is_idempotent` | 1007-1043 | `monkeypatch.setattr(recording, "_scipy_preloader_thread", None)` → `monkeypatch.setattr(resampling, ...)`; same for `_resample_poly`; `recording._start_scipy_preloader()` → `resampling._start_scipy_preloader()`; `recording._scipy_preloader_thread` (reads) → `resampling._scipy_preloader_thread` |
| `TestScipyPreloader.test_start_scipy_preloader_skips_when_scipy_already_loaded` | 1045-1061 | Same as above |

Each migrated test method now imports `resampling` locally and uses it in
place of the package namespace for the `_MUTABLE_*` names.  The
`_RecordingModule` class is **still installed** (per E15) — these migrated
sites simply bypass the routing by writing directly to the submodule.

### Files with NO `_MUTABLE_*` patches found (no migration needed this wave)

Reviewed all 5 in-scope test files plus `tests/test_recorder_secure_clear_array.py`.
Only `tests/test_recording.py` had `_MUTABLE_*` patches.  The other 4 in-scope
files contain:

- **`tests/test_recording_discard.py`** — patches `voice_typer.server.recording.time.sleep`
  (stdlib `time` module via the package re-export) and `rec_mod.time.sleep`
  (object form on the `time` module).  These do NOT go through
  `_RecordingModule.__setattr__` (the dotted-path lookup resolves
  `recording.time` via the package's regular `__dict__` first, yielding the
  `time` module object, then sets `sleep` on it).  Not part of the
  `_RecordingModule` hack — separate cleanup if/when we remove the
  `import time` re-export.
- **`tests/test_recorder_double_resample.py`** — patches `rec_mod.sd.*`
  (object form on the `sounddevice` module) and one already-submodule-direct
  patch of `voice_typer.server.recording.disconnect_handler.retune_audio_processor`.
  No `_MUTABLE_*` usage.
- **`tests/test_recorder_device_cache_prewarm.py`** — only `recording_mod.sd.*`
  object-form patches.  No `_MUTABLE_*` usage.
- **`tests/test_secure_clear_array.py`** — one object-form patch of
  `rec_pkg._secure_clear_array_background`.  This name is NOT in
  `_MUTABLE_BUFFER` (only `_buffer_clear_worker` is).  Production code reads
  it via `_recording_pkg._secure_clear_array_background`, so migrating this
  site requires coordinated production-code changes — out of scope for the
  `_RecordingModule` removal itself (see §3 "Related but separate").
- **`tests/test_recorder_secure_clear_array.py`** — only `inspect.getsource`
  source-string checks; no `monkeypatch.setattr` calls.  Contains one
  docstring mention of the patch pattern (not an actual patch).

### Validation

- `python -m pytest tests/test_recording.py -x -q --no-cov` → 89 passed
  on LINUX (sandbox) (was 89 before migration — no regression, E14).
- `python -m pytest tests/test_recording.py tests/test_recording_discard.py
  tests/test_recorder_double_resample.py tests/test_recorder_device_cache_prewarm.py
  tests/test_secure_clear_array.py tests/test_recorder_secure_clear_array.py
  -q --no-cov` → 141 passed on LINUX (sandbox).
- `python -m pytest tests/test_buffer_clear_worker.py tests/test_retry_regressions.py
  tests/test_recorder_split_start.py tests/test_recording_controller.py
  -q --no-cov` → 77 passed on LINUX (sandbox) — no regression in related
  tests that still use the package-namespace patches (verifies
  `_RecordingModule` routing is still active).

---

## Section 3 — Remaining Work

### 3a. Direct `_MUTABLE_*` patches still on the package namespace

Run on 2026-08-22 after Wave 1 migration:
```
rg 'monkeypatch\.setattr\([^,]*,\s*["\']_?(resample_poly|scipy_preloader_thread|buffer_clear_worker|resample_poly_error)' tests/
```
Output (filtered to package-namespace patches, excluding already-migrated
submodule-direct patches):

| File | Line | Site |
|---|---|---|
| `tests/test_recorder_split_start.py` | 573 | `monkeypatch.setattr(rec_pkg, "_resample_poly", None, raising=False)` |
| `tests/test_recorder_split_start.py` | 574 | `monkeypatch.setattr(rec_pkg, "_resample_poly_error", None, raising=False)` |
| `tests/test_recorder_split_start.py` | 600 | `monkeypatch.setattr(rec_pkg, "_resample_poly", object(), raising=False)` |
| `tests/test_recorder_split_start.py` | 601 | `monkeypatch.setattr(rec_pkg, "_resample_poly_error", None, raising=False)` |
| `tests/test_recorder_split_start.py` | 619 | `monkeypatch.setattr(rec_pkg, "_resample_poly", None, raising=False)` |
| `tests/test_recorder_split_start.py` | 620 | `monkeypatch.setattr(rec_pkg, "_resample_poly_error", RuntimeError("scipy missing"), raising=False)` |

**Subtotal:** 6 patch sites in 1 file.

Plus reads of the package-namespace `_MUTABLE_*` names (these would also
break if `_RecordingModule` were removed without migrating them):

| File | Line | Site |
|---|---|---|
| `tests/test_buffer_clear_worker.py` | 130 | `worker = recording._buffer_clear_worker` (read) |
| `tests/test_buffer_clear_worker.py` | 187 | `worker_after_first = recording._buffer_clear_worker` (read) |
| `tests/test_buffer_clear_worker.py` | 194 | `assert recording._buffer_clear_worker is worker_after_first` (read) |

**Subtotal:** 3 read sites in 1 file.

**Note:** `tests/test_recording.py` lines 992 and 994 contain the literal
string `"recording._scipy_preloader_thread"` inside a Python source string
that is executed in a subprocess (test
`test_no_scipy_preloader_thread_after_pure_import`).  This is a behavioral
test of the package's import-time side effects — it must continue to read
via the `recording` package namespace so that the test remains valid even
after `_RecordingModule` is removed (because `_scipy_preloader_thread` is
re-exported via `from .resampling import _scipy_preloader_thread` in
`__init__.py`, the package-namespace read will continue to work without
`_RecordingModule`).  **No migration needed for this site** — the test
exercises the public package API, not the routing hack.

### 3b. Total remaining `_MUTABLE_*` sites

**9 sites in 2 test files** (6 patches + 3 reads) — to be migrated in a
future wave (Wave 3 or Wave 5 per orchestrator's plan).

### 3c. Related but separate (NOT blocking `_RecordingModule` removal)

These patches use the package namespace but target names that are NOT in
`_MUTABLE_*` — they are regular re-exports, and migrating them requires
coordinated production-code changes (changing `_recording_pkg.X` →
`resampling.X` or `buffer.X` at the call sites in `recorder.py`,
`audio_pipeline.py`, `_recorder_split.py`, `disconnect_handler.py`, etc.):

| Patched name | Defined in | Production call sites | Count |
|---|---|---|---|
| `_get_resample_poly` | `resampling.py` | `_recording_pkg._get_resample_poly()` in `recorder.py:1617`, `audio_pipeline.py:578`, `resampling.py:449` | 9 sites in `tests/test_recording.py` (string form) |
| `_secure_clear_array_background` | `buffer.py` | `_recording_pkg._secure_clear_array_background(...)` in `disconnect_handler.py:417`, `_recorder_split.py:531,1131` | 1 site in `tests/test_secure_clear_array.py` (object form) |

These can be migrated as a separate refactor (e.g., "S1-CR-67 phase 2")
once the `_MUTABLE_*` cleanup is done.  They do NOT block the
`_RecordingModule` removal.

### 3d. Stdlib re-export patches (NOT blocking `_RecordingModule` removal)

Patches like `voice_typer.server.recording.time.sleep`,
`voice_typer.server.recording.os.X`, `voice_typer.server.prewarm.os.X`,
etc. go through the package's regular `__dict__` (NOT through
`_RecordingModule`'s `__getattr__/__setattr__`).  They work because the
package re-exports the stdlib module via `import time` etc.  These would
break only if those `import` statements were removed from `__init__.py` —
a separate cleanup, not part of the `_RecordingModule` removal.

---

## Section 4 — When to Remove the Custom Module Classes

`_RecordingModule` (and the `_MUTABLE_RESAMPLING` / `_MUTABLE_BUFFER`
frozensets, and the `sys.modules[__name__].__class__ = _RecordingModule`
line at the bottom of `recording/__init__.py`) may be deleted once:

1. The count of remaining `_MUTABLE_*` patch sites in §3b reaches **0**
   (currently **9 sites in 2 test files**).
2. A full test-suite run (`python -m pytest tests/ --no-cov -q`) passes
   after the deletion.

**Removal procedure:**
1. Migrate the 9 remaining sites in `tests/test_recorder_split_start.py`
   (6 patches) and `tests/test_buffer_clear_worker.py` (3 reads) following
   the pattern established in this wave (see §2).
2. Delete lines 348-475 of `voice_typer/server/recording/__init__.py`
   (the `_MUTABLE_*` frozensets, the `_RecordingModule` class, and the
   `sys.modules[__name__].__class__ = _RecordingModule` line).
3. Also remove the now-stale `import sys` at line 393 if it is no longer
   used elsewhere in the file (it currently is used only by the
   `_RecordingModule` block).
4. Update the file's top-level docstring (lines 1-90) to remove
   references to the `_RecordingModule` routing (those references are
   documentation of the now-removed hack).
5. Run the full test suite.  If any test fails because it relied on the
   routing, migrate it too and re-run.

**Estimated scope for removal wave:** 1 sub-agent, ~10 min (9 sites +
deletion + verification).

---

## References

- `review.md` entry #4 (S1-CR-67) lines 205-210.
- `voice_typer/server/recording/__init__.py` lines 348-475 (the
  `_RecordingModule` class + `_MUTABLE_*` frozensets).
- `voice_typer/server/recording/resampling.py` (owns `_resample_poly`,
  `_resample_poly_error`, `_resample_poly_error_time`,
  `_scipy_preloader_thread`, `_RESAMPLE_RETRY_INTERVAL`,
  `_get_resample_poly`, `_start_scipy_preloader`).
- `voice_typer/server/recording/buffer.py` (owns `_buffer_clear_worker`,
  `_secure_clear_array_background`).
- `docs/rw9-god-class-decomposition.md` (the original migration plan
  reference; this ADR is the per-wave execution log).
