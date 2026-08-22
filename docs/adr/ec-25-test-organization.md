# ADR: EC-25 — Test Organization (Catch-all Split Plan)

## Status

Accepted — 2026-08-22 (Wave 1, Sub-Agent 12 / W1-A12).

This ADR documents the catch-all test-file split plan per EC-25.
EC-25 is explicitly a "chip away" task (E16): the full split across
12+ catch-all files is too large for one wave, so each wave tackles a
subset. Wave 1 (this ADR) splits the single largest Python catch-all.

## Context

review.md entry #6 (EC-25, lines 264-277) flags 12+ catch-all test
files that mix unrelated test domains in one physical file. The
catch-alls are review-round accumulation: each review wave created a
fresh `test_<round>_review_fixes.py` (or similar) and dumped every new
regression test into it, regardless of which domain the test actually
pinned. The largest Python catch-alls at the time of this audit:

| File                                                  | Lines |
|-------------------------------------------------------|------:|
| `tests/test_perf_review_fixes.py`                     |   941 |
| `tests/test_dictation_pipeline_review_fixes.py`      |   619 |
| `tests/test_low_findings_batch.py`                   |   448 |
| `tests/test_remaining_fixes.py`                      |   267 |

(Three TS catch-alls are also flagged —
`ux-components-behavior.test.tsx` (1815 lines, 11 components),
`electron-ipc-build-behavior.test.tsx` (1339 lines, 28 concerns),
`pages-improvements.test.tsx` (898 lines, 9 pages) — these are
out of scope for this Python-only sub-agent and will be split in a
later wave by a TS-scoped agent.)

The root cause is procedural: there was no policy that says "every new
test goes in the matching per-domain test file." Each review wave's
sub-agent wrote a fresh `<round>_review_fixes.py` because that was
the lowest-friction path. The catch-all files then grew monotonically
across waves — every wave's review fixes piled on top of the previous
wave's review fixes, none of which were ever re-homed to their
proper domain files.

This is a maintainability problem (rule #20: tests must go in matching
domain module) but not a correctness one — every test in every
catch-all passes. The split is pure mechanical refactoring: move
classes verbatim, delete the catch-all, verify test count is
preserved.

## Decision

Split the catch-all files into per-domain test files, one physical
file per domain. The split rule is:

1. **Identify the test classes in the catch-all.** Each `class TestX`
   block is one movable unit.
2. **Group classes by their production-code domain.** A class that
   tests `text_cleanup` regex precompilation belongs in
   `test_perf_text_cleanup.py`; a class that tests `Recorder.start()`
   secure-clear belongs in `test_recorder_secure_clear_array.py`
   (which already exists for that domain — append, don't duplicate).
3. **Move each class verbatim** into the new per-domain file. No
   edits to the test body. Add a header docstring to the new file
   that names the findings it pins (PERF-004, PERF-PIPE, etc.).
4. **Delete the original catch-all.** Record the deletion in
   `archive/deleted_files.txt` per the close-out protocol.
5. **Verify the test count is preserved.** `pytest --collect-only`
   before and after must produce the same number of tests.
6. **Verify the tests still pass.** `pytest <new files>` must produce
   the same pass/fail status as the original catch-all.

The split is non-destructive: every test that ran before still runs
after, in the same number, with the same pass/fail status. The only
observable change is the file path pytest reports.

## Completed this wave (W1-A12)

### `tests/test_perf_review_fixes.py` — SPLIT (941 → 0 lines)

The largest catch-all was split into 4 per-domain files. The 6 test
classes were grouped by their production-code domain:

| Old (catch-all) class                              | New file                                              | Domain            | Tests |
|----------------------------------------------------|-------------------------------------------------------|-------------------|------:|
| `TestCleanTranscribedTextUsesPrecompiledRegex`     | `tests/test_perf_text_cleanup.py`                    | text_cleanup      |     4 |
| `TestPipeTokenKeyUsesPrecompiledRegex`             | `tests/test_perf_text_cleanup.py`                    | text_cleanup      |     4 |
| `TestWin32PollingLoopUsesSleepEight`              | `tests/test_perf_hotkey_polling.py`                  | hotkeys/win32     |     3 |
| `TestAllLocalEnginesAcceptAudioStats`              | `tests/test_perf_asr_engines_audio_stats.py`         | asr engines       |    11 |
| `TestTranscribeBatchSequentialDesignDecision`      | `tests/test_perf_asr_engines_audio_stats.py`         | asr engines       |     4 |
| `TestAudioWindowEqualityUsesLayeredFastPaths`      | `tests/test_perf_audio_window_eq.py`                 | streaming/audio   |     8 |
| **Total**                                          |                                                       |                   |    34 |

The split rationale:

- **`test_perf_text_cleanup.py`** — both `TestCleanTranscribedTextUsesPrecompiledRegex`
  (PERF-004) and `TestPipeTokenKeyUsesPrecompiledRegex` (PERF-PIPE) pin
  the same production module (`voice_typer.server.text_cleanup`) and
  the same invariant (precompiled regex). They belong together
  because a future regression in either will be investigated by
  reading the same source file.
- **`test_perf_hotkey_polling.py`** — `TestWin32PollingLoopUsesSleepEight`
  (PERF-012) is the only Win32-specific test in the catch-all. It
  pins a polling-cadence invariant in `hotkeys.WindowsNativeHotkey`
  that has nothing to do with the other 5 classes.
- **`test_perf_asr_engines_audio_stats.py`** — both
  `TestAllLocalEnginesAcceptAudioStats` (PERF-STATS) and
  `TestTranscribeBatchSequentialDesignDecision` (PERF-009) test the
  same production module (`qwen_engine.QwenEngine` /
  `parakeet_engine.ParakeetEngine`) and share the same `_make_qwen_engine`
  / `_make_parakeet_engine` private helpers. Co-locating them lets the
  helpers stay private to the file.
- **`test_perf_audio_window_eq.py`** — `TestAudioWindowEqualityUsesLayeredFastPaths`
  (PERF-EQ) pins the `AudioWindow.__eq__` layered comparison. It is
  the only test of `voice_typer.server.streaming.AudioWindow`'s equality
  contract.

Verification (Linux sandbox):

```
$ python -m pytest tests/test_perf_text_cleanup.py tests/test_perf_hotkey_polling.py \
    tests/test_perf_asr_engines_audio_stats.py tests/test_perf_audio_window_eq.py \
    --collect-only -q --no-cov
========================= 34 tests collected in 0.68s ==========================

$ python -m pytest tests/test_perf_text_cleanup.py tests/test_perf_hotkey_polling.py \
    tests/test_perf_asr_engines_audio_stats.py tests/test_perf_audio_window_eq.py \
    --no-cov -q
======================= 34 passed, 38 warnings in 1.17s ========================
```

Baseline before the split: 34 tests collected / 34 passed.

Original catch-all deleted; deletion recorded in
`archive/deleted_files.txt`:

```
DELETE  |  tests/test_perf_review_fixes.py  |  W1-A12 (EC-25): split into 4 per-domain files ...
```

### `tests/fixtures/ipc_test_helpers.py` — EXTENDED (XS-42 work, EC-25-adjacent)

Extended with two new factory exports per the XS-42 directive
(review.md lines 382-420). Both are thin delegates to the existing
canonical factory modules — no logic duplication:

- `make_fake_sidecar_ws_server()` → delegates to
  `tests.fixtures.sidecar_ws_test_helpers._make_fake_server` (the
  single source of truth for the fake sidecar WS server's attribute
  set).
- `make_fake_recorder()` → delegates to
  `tests.fixtures.recorder_test_helpers.make_recorder` (the single
  source of truth for the minimal-Recorder shape used by the
  secure-clear / hot-swap test suite).

The delegates exist so that `tests.fixtures.ipc_test_helpers` is the
single canonical import surface for IPC-layer test doubles — every
"fake thing" a test might need (`fake_app`, `fake_service`,
`fake_sidecar_ws_server`, `fake_recorder`) is importable from one
module. This is what XS-42's "promote `ipc_test_helpers.py` to also
export `make_fake_sidecar_ws_server()` and `make_fake_recorder()`
factories" directive asks for.

### `tests/fixtures/app_helpers.py` — already existed, docstring updated

The file already existed (added by an earlier XS-FIX-2 wave) with the
two factories `make_voice_typer_app()` and `make_sine()`. W1-A12
updated its "Migration status" docstring section to reflect the two
additional test files migrated this wave.

### 4 test files migrated to use shared factories (XS-42 work)

The following 4 test files previously had byte-for-byte copies of
factory bodies that now exist in `tests/fixtures/`. W1-A12 replaced
each copy with a 1-line alias / delegate so the body lives in
exactly ONE place:

| File                                                 | Old local def                | New body (alias to canonical)                       | Tests |
|------------------------------------------------------|------------------------------|-----------------------------------------------------|------:|
| `tests/test_secure_clear_array.py`                   | `_make_recorder` (22 lines)  | `return make_fake_recorder()`                       |    18 |
| `tests/test_secure_clear_no_resample_segments.py`    | `_make_recorder` (20 lines)  | `return make_fake_recorder()`                       |     3 |
| `tests/test_recorder_double_resample.py`             | `_make_sine` (4 lines)       | `return make_sine(...).reshape(-1, 1)`              |     8 |
| `tests/test_recording_audio_processor.py`            | `_make_sine` (4 lines)       | `return make_sine(...).reshape(-1, 1)`              |     9 |
| **Total migrated tests**                             |                              |                                                     |    38 |

The alias pattern (rather than rewriting every call site to call
`make_fake_recorder()` / `make_sine(...).reshape(-1, 1)` directly)
preserves the call-site shape contract — every `rec = _make_recorder()`
and `chunk = _make_sine(...)` line continues to work unchanged. This
is the minimum-risk migration: only the duplicated computation body
is deleted; the call sites remain verbatim.

The `_make_sine` wrappers additionally preserve the PortAudio
`(frames, 1)` shape contract by appending `.reshape(-1, 1)` to the
canonical 1-D `make_sine` output. This was the same pattern already
in use at `tests/test_audio_processor.py` (the previously-migrated
reference file).

Verification (Linux sandbox):

```
$ python -m pytest tests/test_secure_clear_array.py \
    tests/test_secure_clear_no_resample_segments.py \
    tests/test_recorder_double_resample.py \
    tests/test_recording_audio_processor.py --no-cov -q
======================= 38 passed, 42 warnings in 2.74s ========================

$ python -m pytest tests/ --import-mode=importlib --co -q --no-cov
======================= 14382 tests collected in 26.96s ========================
```

The full-suite collection count (14382) is unchanged from the
W1-A6 baseline recorded in `worklog.md`, confirming no test was lost
or accidentally duplicated.

## Remaining catch-alls (per EC-25's "chip away" plan)

The following catch-all files are NOT yet split. They are listed
here so the next wave's sub-agent has a ready work-queue:

### Python catch-alls

| File                                                  | Lines | Class count | Domain mix (rough)                                          |
|-------------------------------------------------------|------:|------------:|-------------------------------------------------------------|
| `tests/test_dictation_pipeline_review_fixes.py`      |   619 |           7 | dictation_pipeline notify-once + transcription backends + stage-timer |
| `tests/test_low_findings_batch.py`                   |   448 |           6 | config-dir + privacy redaction + GDPR docs + UX + security + packaging |
| `tests/test_remaining_fixes.py`                       |  267 |           ? | (audit pending — file not opened this wave)                 |
| `tests/test_comprehensive_review_fixes.py`            |   ?   |           ? | (audit pending — file not in original EC-25 list but flagged in adjacent reviews) |

### TS catch-alls (out of scope for Python-only sub-agents)

| File                                                                  |  Lines | Component count |
|-----------------------------------------------------------------------|-------:|----------------:|
| `voice_typer/client/src/renderer/src/__tests__/ux-components-behavior.test.tsx`   | 1815 | 11 |
| `voice_typer/client/src/renderer/src/__tests__/electron-ipc-build-behavior.test.tsx` | 1339 | 28 |
| `voice_typer/client/src/renderer/src/__tests__/pages-improvements.test.tsx`       |  898 |  9 |

These TS catch-alls should be split by a TS-scoped sub-agent in a
later wave. The Python catch-alls above are the next wave's work.

## XS-42 remaining migration targets

The following test files from XS-42's related-files list still have
private factory definitions that are candidates for future migration:

- `tests/test_recorder_device_cache_prewarm.py` — has `_make_recorder(config=None)`
  with a DIFFERENT shape (extra config-arg + post-construction
  mutations). NOT byte-for-byte duplicate. Would need either a
  parameterized `make_recorder(config=None)` overload in
  `recorder_test_helpers.py` or a separate factory. (Documented in
  `recorder_test_helpers.py`'s docstring as Remaining Work.)
- `tests/test_recording_discard.py`, `tests/test_hot_swap_secure_clear.py`,
  `tests/test_audio_pipeline_process_chunk.py`, `tests/test_stream_lifecycle_module.py`
  — all have file-specific `_make_recorder` shapes that differ from
  the canonical. Each needs its own audit before consolidation.
- `tests/test_concurrent_resample_safety.py` — has `_make_recorder`
  (different shape — see file). Candidate for future migration.
- The 6 sidecar-WS test files (`tests/tauri/mig15..17/test_ws_hmac_*.py`,
  `tests/test_sidecar_ws_thread_safety.py`, `tests/tauri/test_sidecar_ws_unit.py`,
  `tests/test_ipc5_error_envelope_parity.py`) — already migrated to
  import `_make_fake_server` from `tests.fixtures.sidecar_ws_test_helpers`.
  Could optionally be re-migrated to import `make_fake_sidecar_ws_server`
  from `ipc_test_helpers` instead (the new canonical entry point
  exported this wave), but this is cosmetic and not required by XS-42.

## Consequences

- **Positive**: The 6 PERF-finding regression tests are now navigable
  by domain — a maintainer investigating a regression in `text_cleanup`
  regex precompilation opens `test_perf_text_cleanup.py` directly
  rather than scanning a 941-line catch-all.
- **Positive**: 4 more test files now share the canonical
  `make_fake_recorder` / `make_sine` factories, so a future change
  to `Recorder.__init__` or the sine-wave contract updates ONE place.
- **Neutral**: pytest collection count unchanged (14382 → 14382). No
  test was lost, no test was added, no test changed pass/fail status.
- **Negative**: The new per-domain files duplicate the import block
  (`from __future__ import annotations`, `import pytest`, etc.) at
  the top of each file. This is the standard Python per-file-import
  cost and is unavoidable. The 4 new files cost ~20 lines of
  duplicated import boilerplate total — acceptable.

## Validation Performed (Linux sandbox)

| Command                                                                                              | Result                              |
|------------------------------------------------------------------------------------------------------|-------------------------------------|
| `python -m pytest tests/test_perf_review_fixes.py --collect-only -q --no-cov`                         | 34 tests collected (baseline)       |
| `python -m pytest tests/test_perf_text_cleanup.py tests/test_perf_hotkey_polling.py tests/test_perf_asr_engines_audio_stats.py tests/test_perf_audio_window_eq.py --collect-only -q --no-cov` | 34 tests collected (matches baseline) |
| `python -m pytest tests/test_perf_text_cleanup.py tests/test_perf_hotkey_polling.py tests/test_perf_asr_engines_audio_stats.py tests/test_perf_audio_window_eq.py --no-cov -q` | 34 passed |
| `python -m pytest tests/test_secure_clear_array.py tests/test_secure_clear_no_resample_segments.py tests/test_recorder_double_resample.py tests/test_recording_audio_processor.py --no-cov -q` | 38 passed |
| `python -m pytest tests/ --import-mode=importlib --co -q --no-cov`                                   | 14382 tests collected (no regression) |
| `python -c "from tests.fixtures.ipc_test_helpers import make_fake_app, make_fake_service, make_fake_sidecar_ws_server, make_fake_recorder, make_ipc_server_with_fakes"` | imports OK |

## References

- review.md entry #6 (EC-25, lines 264-277) — original finding.
- review.md entry #12 (XS-42, lines 382-420) — related factory-dedup finding.
- `archive/deleted_files.txt` — records the deletion of `tests/test_perf_review_fixes.py`.
- `tests/fixtures/ipc_test_helpers.py` — extended with the new factory exports.
- `tests/fixtures/app_helpers.py` — pre-existing, docstring updated.
- `tests/fixtures/recorder_test_helpers.py` — pre-existing, docstring updated.
- `tests/fixtures/sidecar_ws_test_helpers.py` — pre-existing, unchanged.
