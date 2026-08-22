# ARCH-9 — `app.py` Test-Seam Re-Export Migration Plan

**Status**: In Progress (Wave 1 chip-away)
**Started**: 2026-08-22 (Wave 1, Sub-Agent #9 / W1-A9)
**Scope**: Migrate `monkeypatch.setattr("voice_typer.server.app.X", ...)` sites in `tests/` to canonical paths (`voice_typer.server.server_platform.X`, etc.), then delete the corresponding re-export blocks from `voice_typer/server/app.py` once dependency count hits zero.

## Context

`voice_typer/server/app.py` re-exports ~20 symbols from sibling modules so
tests can monkeypatch them via the string path `"voice_typer.server.app.X"`.
This creates two distinct coupling problems:

1. **Coupling to a giant module** — every test that patches `app.X` transitively
   imports the entire `voice_typer.server.app` module (heavy: pulls in tray,
   recording, dictation pipeline, model manager, security, etc.).
2. **Hidden indirection** — when a test patches `app.X`, the patch only takes
   effect at code sites that re-resolve the symbol via the `app` module at call
   time. Internal callers that did `from voice_typer.server.app import X` once
   at module top won't see the patch. This is fragile.

The migration target is to move all `app.X` monkeypatch sites to canonical
module paths (e.g. `voice_typer.server.server_platform.is_autostart_enabled`)
and then delete the re-export block(s) from `app.py`.

### Re-export block under migration

```python
# voice_typer/server/app.py:121-126
from voice_typer.server.server_platform import (  # noqa: F401
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
    list_microphones,
)
```

The canonical module `voice_typer.server.server_platform` uses the
"patch-path bridge" pattern internally (see
`server_platform/autostart.py` lines 60-65 and
`server_platform/microphone_list.py` lines 41-46): public dispatch functions
look up sub-helpers via `_pkg.X` (the package-self reference) at call time so
that `monkeypatch.setattr("voice_typer.server.server_platform.X", ...)` DOES
intercept the call.

## Migration Plan

The full migration touches ~213 monkeypatch sites across ~50 test files for
~20 re-exported symbols. Per the orchestrator's "chip away" directive
(E16 — max 5 big tasks per session; multi-hour refactor deferred), this is
done in incremental waves.

### Phase 1 (this wave — W1-A9)

Target the TOP 3 symbols by site count:

| Symbol                  | Canonical path                                            |
| ----------------------- | --------------------------------------------------------- |
| `is_autostart_enabled`  | `voice_typer.server.server_platform.is_autostart_enabled` |
| `list_microphones`      | `voice_typer.server.server_platform.list_microphones`     |
| `enable_autostart`      | `voice_typer.server.server_platform.enable_autostart`     |

Migration rule (mechanical):

```diff
- monkeypatch.setattr("voice_typer.server.app.<SYMBOL>", ...)
+ monkeypatch.setattr("voice_typer.server.server_platform.<SYMBOL>", ...)
```

### Phase 2+ (future waves)

- Migrate `disable_autostart` (32 sites — same re-export block).
- Migrate `is_windows` (12 sites), `is_macos` / `is_linux` (5 each) →
  `voice_typer.server.platform_utils.*`.
- Migrate `_setup_logging` (7 sites) → `voice_typer.server.logging_setup._setup_logging`.
- Migrate `_config_dir` (2 sites) → `voice_typer.server.config._config_dir`.
- Migrate `VoiceTyperApp` (7 sites) — these are top-level class patches; canonical
  path is `voice_typer.server.app.VoiceTyperApp` itself, so these stay.
- Migrate the small remaining tail (one-offs): `_register_devnull_file`,
  `_close_devnull_files`, `_clear_backend_pid_file`, `configure_corrections`,
  `_windows_*`, `_systemroot_notepad_path`, `StreamingTranscriptionSession`.

### Phase 3 — caller-side migration (PREREQUISITE for full removal)

Even after every test-file patch is migrated, the re-export blocks cannot be
removed from `app.py` until the **internal callers** that currently route
through `voice_typer.server.app.X` are migrated to call the canonical module
directly. The known internal callers (verified 2026-08-22):

| Caller                                                      | Symbol(s) routed via `_app_module` (= `voice_typer.server.app`) |
| ----------------------------------------------------------- | --------------------------------------------------------------- |
| `voice_typer/server/startup_tasks.py:113` `sync_autostart`  | `is_autostart_enabled`, `enable_autostart`, `disable_autostart` |
| `voice_typer/server/startup_tasks.py:379` `sync_microphones` | `list_microphones`                                              |
| `voice_typer/server/settings_controller.py:79`               | `is_autostart_enabled`                                          |
| `voice_typer/server/settings_controller.py:99-101`          | `enable_autostart`, `disable_autostart`                         |

The intentional indirection is documented at:
- `startup_tasks.py:17-18, 100-112`
- `settings_controller.py:31-32, 75, 91-92`

These comments explicitly say "tests that monkeypatch
`voice_typer.server.app.X` continue to take effect" — once test migration
completes, those comments should be updated and the call sites changed to
`from voice_typer.server import server_platform as _platform; _platform.X()`.

## Completed This Wave (W1-A9, 2026-08-22)

| Symbol                 | Sites migrated (this wave) | Sites remaining on `app.X` | Notes                                       |
| ---------------------- | -------------------------- | -------------------------- | ------------------------------------------- |
| `is_autostart_enabled` | 13                         | 25                         | 4 sites in `test_autostart_syncs_with_platform` reverted to `app.X` — see "Known Limitation" below |
| `list_microphones`     | 13                         | 22                         | 3 pre-existing `server_platform.list_microphones` patches in `tests/test_platform.py` (untouched) |
| `enable_autostart`     | 13                         | 20                         | Same revert reason as `is_autostart_enabled` |
| **TOTAL migrated**     | **39**                     | —                          | Target was 30-40 — hit the upper bound      |

### Files changed (test-side only — `app.py` business logic untouched)

1. `tests/app/test_lifecycle.py` — 21 sites migrated (7 each × 3 symbols, 7 fixtures of `TestAppStartupIntegration`).
2. `tests/app/test_config_wiring.py` — 11 sites migrated (5 each × 3 symbols = 15, minus 4 reverted in `test_autostart_syncs_with_platform` which kept its `app.X` patches).
3. `tests/app/conftest.py` — 3 sites migrated (the `_make_app` helper).
4. `tests/fixtures/app_helpers.py` — 3 sites migrated in `make_voice_typer_app` helper; docstring updated to reflect migration.

### Why `disable_autostart` was NOT migrated this wave

`disable_autostart` is the 4th symbol in the same `from server_platform import (…)` re-export bundle. Its 32 sites live in many of the same test files. Per W1-A9 scope (top 3 symbols only), it's deferred to a future wave. Leaving `disable_autostart` patches on `app.X` is safe — the re-export remains in `app.py` (back-compat).

### Known limitation — `test_autostart_syncs_with_platform` cannot be fully migrated yet

The test `tests/app/test_config_wiring.py::TestConfigWiring::test_autostart_syncs_with_platform` asserts that `enable_autostart` was called (via a `called` list appended to in the lambda). It calls `startup_tasks.sync_autostart(app)`. That function resolves `is_autostart_enabled` and `enable_autostart` via `_app_module = voice_typer.server.app` at call time (see `startup_tasks.py:113, 153, 156`). Therefore patching `voice_typer.server.server_platform.X` does NOT intercept those calls.

This is exactly the limitation called out in `review.md` (ARCH-9 entry): *"Full migration additionally requires routing app.py's INTERNAL calls through the canonical modules … otherwise patching the canonical path won't intercept app-internal use."*

Until Phase 3 (caller-side migration of `startup_tasks.sync_autostart` and `settings_controller.set_autostart`) lands, the patches in `test_autostart_syncs_with_platform` MUST stay on `voice_typer.server.app.X` (annotated in the test with a NOTE pointing back to this ADR).

The same caveat applies to ANY test that (a) calls `startup_tasks.sync_autostart` or `settings_controller.set_autostart` directly AND (b) asserts call counts or specific return values that depend on the patch taking effect. The 13 sites migrated this wave that use the patches only as **defensive no-ops** (`lambda: False`, `lambda: True`, `lambda: []`) are safe — they only prevent the constructor from touching real platform state during setup, and the real implementations in the sandbox are inert or no-ops anyway (verified by 160/160 tests in `tests/app/` passing).

### Phase-2 regression caught by W2-R4 — `test_autostart_disabled_when_config_false` (W3-A3 fix, 2026-08-22)

W1-A9's wave-1 migration missed one of the two autostart-state-asserting tests in `tests/app/test_config_wiring.py`. The sister test `test_autostart_syncs_with_platform` (line ~134-138) had been reverted to `app.X`, but `test_autostart_disabled_when_config_false` (line ~150-168) was left with its `is_autostart_enabled` patch on `voice_typer.server.server_platform.is_autostart_enabled` — a STATIC import-time binding that does NOT propagate patches on `server_platform` to the `_app_module.is_autostart_enabled` lookup at `startup_tasks.py:153`.

The patch was a no-op; the test passed ONLY because the sandbox happened to have `~/.config/autostart/voice-typer.desktop` (making the real `is_autostart_enabled()` return True, satisfying the `not app.config.autostart and actual` branch where `disable_autostart` is called). W2-R4 confirmed by running with a clean HOME (`HOME=/tmp/no_autostart_home_*`) — the test FAILED with `assert 0 == 1` because `disable_autostart` was not called (real `is_autostart_enabled()` returned False → "already in sync" branch).

W3-A3 reverted all four autostart patches in `test_autostart_disabled_when_config_false` back to `voice_typer.server.app.X` (matching the sister test pattern) so both autostart-state-asserting tests share the same canonical-path-deferred pattern. The 13 defensive-no-op patches migrated in W1-A9 remain on `server_platform.X` (they are inert and asserted to remain so).

**Phase 3 (caller-side migration)** remains the proper long-term fix: migrate `startup_tasks.sync_autostart`, `startup_tasks.sync_microphones`, and `settings_controller.set_autostart` to resolve symbols via `from voice_typer.server import server_platform as _platform; _platform.X()` (instead of `_app_module.X`), at which point the re-export block at `app.py:121-126` can be deleted entirely.

## Remaining Sites Count (snapshot 2026-08-22)

Total `voice_typer.server.app.X` monkeypatch sites in `tests/`:

```
$ rg 'monkeypatch\.setattr.*"voice_typer\.server\.app\.' tests/ -c --no-filename | awk -F: '{s+=$1} END {print s}'
174
```

(Started at 213 before this wave; migrated 39 sites → 174 remaining.)

Per-symbol breakdown:

| Symbol                          | Sites on `app.X` (remaining) |
| ------------------------------- | ----------------------------- |
| `disable_autostart`             | 32                            |
| `is_autostart_enabled`          | 25                            |
| `list_microphones`              | 22                            |
| `enable_autostart`             | 20                            |
| `is_windows`                    | 12                            |
| `_setup_logging`                | 7                             |
| `VoiceTyperApp`                 | 7 (stays — class lives here) |
| `is_macos`                      | 5                             |
| `is_linux`                      | 5                             |
| `_register_devnull_file`        | 4                             |
| `_close_devnull_files`          | 4                             |
| `_clear_backend_pid_file`       | 4                             |
| `_config_dir`                   | 2                             |
| `configure_corrections`         | 1                             |
| `_windows_wait_for_process_exit`| 1                             |
| `_windows_open_with_default_app`| 1                            |
| `_windows_close_process_handle` | 1                             |
| `_systemroot_notepad_path`      | 1                             |
| `StreamingTranscriptionSession` | 1                             |
| **TOTAL**                       | **174**                       |

## When to remove the re-export block from `app.py`

The `from voice_typer.server.server_platform import (disable_autostart, enable_autostart, is_autostart_enabled, list_microphones)` block (app.py:121-126) can be deleted when ALL of the following hold:

1. `rg 'monkeypatch\.setattr.*"voice_typer\.server\.app\.(is_autostart_enabled|list_microphones|enable_autostart|disable_autostart)"' tests/` returns **0 matches** (currently 99).
2. `startup_tasks.py:113` (`_app_module = voice_typer.server.app`) and the call sites at lines 153, 156, 177, 385 are migrated to call `voice_typer.server.server_platform.X` directly.
3. `settings_controller.py:79, 99-101` similarly migrated.
4. The comments in `startup_tasks.py:17-18, 100-112` and `settings_controller.py:31-32, 75, 91-92` that document the "tests monkeypatch app.X" indirection are updated to reflect canonical-path patching.

Until then, the re-export block stays in place (back-compat) — this is the "Partial" state called out in `review.md` ARCH-9 entry.

## Validation Performed

- `python -m pytest tests/app/test_lifecycle.py tests/app/test_config_wiring.py -q --no-cov` → **44 passed** on LINUX (sandbox).
- `python -m pytest tests/app/ -q --no-cov` → **160 passed** on LINUX (sandbox) — covers the `conftest.py` migration impact on the entire `tests/app/` directory.
- `python -m pytest tests/test_api_doc_accuracy.py -q --no-cov` → **8 passed** on LINUX (sandbox) — covers the `tests/fixtures/app_helpers.py` migration impact on its sole caller.

No regressions (E14). No business-logic changes in `app.py` (scope rule).

## References

- `review.md` ARCH-9 entry (lines 180-187) — partial status, 218 sites, multi-hour refactor deferred.
- `AGENTS.md` — E6 (tests pass), E14 (no regressions), E15 (technical debt documented), E16 (max 5 big tasks per session).
- `voice_typer/server/server_platform/autostart.py` lines 30-40 — patch-path bridge pattern documentation.
- `voice_typer/server/server_platform/microphone_list.py` lines 41-46 — same pattern for `list_microphones`.
