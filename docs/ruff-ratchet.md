# Ruff Ratchet

This document explains the **ruff ratchet** — the CI mechanism that
prevents new ruff violations from being introduced without breaking the
build on the (small) set of pre-existing violations that have not been
fixed yet.

## TL;DR

* **F-rules** (pyflakes: unused imports, undefined names, redefinitions)
  are a **hard-fail** in CI. Zero tolerance. Fix them immediately.
* **All other configured rules** (E, W, I, N, UP, B, A, SIM — see
  `pyproject.toml` `[tool.ruff.lint] select`) are tracked via a
  **ratchet**: a baseline file (`ruff-baseline.json`) records the
  current violation count. CI fails if the count **grows**. The count
  is allowed to **stay the same** or **shrink**.
* **Never edit the baseline to add violations.** The ratchet only ever
  moves in one direction: down. If you fix violations, regenerate the
  baseline to lock in the new (lower) floor.

## Why a ratchet?

Before RW-11, the CI step was:

```yaml
ruff check voice_typer/server/ --select E501
```

The `--select E501` flag **overrides** the `select` array in
`pyproject.toml`, so CI was checking *only* E501 (line-too-long) and
silently bypassing the other ~99% of configured rules. Removing
`--select E501` would have surfaced ~729 errors at the time RW-11 was
filed.

A blanket hard-fail on every configured rule would break CI on the
first new style nit, even if that nit is harmless. The ratchet is the
middle ground:

* Real bugs (F-rules) are blocked immediately.
* Style nits are tracked but don't break CI unless they *grow* the
  existing count. Existing nits can be cleaned up incrementally.

## Files

| File | Purpose |
|---|---|
| `ruff-baseline.json` | The ratchet floor. Snapshot of current violations. |
| `scripts/ruff_ratchet_check.py` | Comparison + regeneration script. Run by CI. |
| `.github/workflows/build.yml` | CI workflow with two ruff steps (hard-fail + ratchet). |
| `tests/test_ruff_ratchet.py` | Test that the baseline file is valid and matches the schema. |

## CI behaviour

The CI workflow has two ruff steps that run on every PR and push:

### Step 1: Ruff (F-rules hard-fail)

```yaml
python -m ruff check voice_typer/server/ --select F --no-fix
```

* Scope: `voice_typer/server/` only.
* Selects: `F` (pyflakes — real bugs).
* `--no-fix`: never auto-fix; the developer must fix and commit.
* Exit code 1 → CI fails. No baseline, no soft-fail. Fix the bug.

### Step 2: Ruff (ratchet compare against baseline)

```yaml
python -m ruff check voice_typer/server/ --output-format=json > ruff-current.json || true
python scripts/ruff_ratchet_check.py
```

* Scope: `voice_typer/server/` only.
* Selects: every rule configured in `pyproject.toml` `[tool.ruff.lint]
  select` (i.e. `E, F, W, I, N, UP, B, A, SIM`).
* `|| true`: ruff exits 1 when violations are found; we don't care
  about ruff's exit code, only the comparison result.
* The comparison script reads `ruff-current.json` + `ruff-baseline.json`
  and fails CI if:
  * `total_count` grew above the baseline, OR
  * any individual rule's count grew above its baseline value (even if
    the total stayed the same — e.g. UP037 dropped by 2 but F401 grew
    by 2).

## Developer workflow

### You fixed some ruff violations — how to update the baseline

After you fix violations (e.g. you wrapped a long line, removed an
unused import, or split a circular import), the ratchet will report
"improved" but the baseline still records the old (higher) count. Lock
in the gain:

```bash
cd /path/to/voice-typer
source .venv/bin/activate

# Regenerate the baseline from the current ruff output.
ruff check voice_typer/server/ --output-format=json \
  | python scripts/ruff_ratchet_check.py --regenerate --stdin
```

The script **refuses** to regenerate if the new total is *higher* than
the old total — that would be a regression, not a ratchet. If you
genuinely need to grow the baseline (rare — e.g. a new rule was added
to `select`), you must:

1. Discuss in the PR description *why* the baseline needs to grow.
2. Manually edit `ruff-baseline.json` (the script will not do it for
   you).
3. Get reviewer sign-off.

### You introduced a new ruff violation — CI is failing

Two options:

1. **Fix it** (preferred). The ratchet exists to keep you honest; if
   you can fix the violation in 5 minutes, do it.
2. **Justify it** (rare). If the new violation is unavoidable (e.g. a
   third-party API forces a quoted annotation), document why in the PR
   description, manually bump `ruff-baseline.json`, and get reviewer
   sign-off. Do NOT make this a habit — the ratchet only works if the
   baseline trends to zero.

### You added a new rule to `select` in `pyproject.toml`

Adding a new rule (e.g. adding `"C4"` to comprehensions) will surface
new violations across the codebase. The ratchet will fail CI because
the new rule's count is `0` in the baseline but `>0` in current.

To onboard a new rule:

1. Add the rule to `select` in `pyproject.toml`.
2. Run `ruff check voice_typer/server/ --output-format=json` locally
   and inspect the new violations.
3. Either fix them all (preferred) OR add them to the baseline:
   ```bash
   ruff check voice_typer/server/ --output-format=json \
     | python scripts/ruff_ratchet_check.py --regenerate --stdin
   ```
   (The regenerate script refuses to grow the baseline. To onboard a
   new rule, you'll need to manually edit `ruff-baseline.json` to add
   the new rule's count, with a comment in the PR description.)
4. Document the new rule in this file's *Rule onboarding history*
   section below.

## Local pre-flight

Before pushing:

```bash
cd /path/to/voice-typer
source .venv/bin/activate

# Mirror CI Step 1.
ruff check voice_typer/server/ --select F --no-fix

# Mirror CI Step 2.
ruff check voice_typer/server/ --output-format=json > ruff-current.json
python scripts/ruff_ratchet_check.py
```

Or in one shot via stdin (no temp file):

```bash
ruff check voice_typer/server/ --output-format=json \
  | python scripts/ruff_ratchet_check.py --stdin
```

## Schema

`ruff-baseline.json`:

```jsonc
{
  "_comment": "ruff ratchet baseline. ...",   // optional metadata
  "_schema_version": 1,                              // optional metadata
  "_target": "voice_typer/server/",                  // optional metadata
  "total_count": 3,                                  // REQUIRED: non-negative int
  "by_rule": {                                       // REQUIRED: object
    "UP037": 3                                       //   rule_code -> non-negative int
  }
}
```

The only **required** fields are `total_count` and `by_rule`.
Underscore-prefixed metadata fields (`_comment`, `_schema_version`,
`_target`) are preserved by the regenerate script and ignored by the
comparison logic.

`tests/test_ruff_ratchet.py` verifies that the baseline file:

* Is valid JSON.
* Has the required `total_count` (int ≥ 0) and `by_rule` (object) fields.
* Has `total_count == sum(by_rule.values())`.
* Has all `by_rule` values be non-negative ints.
* Is consistent with the *actual* current ruff output (the actual
  count is `<=` the baseline — i.e. the ratchet is not currently
  regressed).

## Rule onboarding history

| Date | Rule | Action | Notes |
|---|---|---|---|
| RW-11 | (initial) | Baseline = 3 UP037 violations in `voice_typer/server/event_bus.py` | Quoted type annotations — `from __future__ import annotations` makes the quotes redundant. Safe to fix with `ruff check --fix`. |

## Out of scope (future work)

* **Expand scope to `voice_typer/` (all packages, not just `server/`).**
  Currently the ratchet only covers `voice_typer/server/` to match the
  pre-RW-11 CI scope. Expanding to `voice_typer/` and `tests/` would
  surface additional violations (e.g. F401/F811 in
  `tests/test_event_bus.py`). Onboard the new scope by adding it to
  the CI step, regenerating the baseline, and fixing or accepting the
  new violations.
* **Auto-fix on CI.** Not done — `--no-fix` is intentional. Developers
  must run `ruff check --fix` locally and review the diff.
