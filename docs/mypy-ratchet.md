# mypy Ratchet

This document explains the **mypy ratchet** — the mechanism that
prevents new mypy errors from being introduced without failing the
pre-push hook, while keeping the (large) set of pre-existing typing-debt
errors non-blocking.

## TL;DR

* mypy is **not** a clean gate for this codebase today: numpy 2.x's
  stubs use Python 3.12-only `type` syntax that mypy cannot even *parse*
  at the project's mypy language level (`python_version = "3.10"`,
  CQ-058). A parse error cannot be suppressed via `ignore_errors` or
  `follow_imports` (verified empirically on mypy 1.x and 2.x).
* numpy is therefore **shadowed by a local Any-stub**
  (`voice_typer/mypy_stubs/numpy/` via `[tool.mypy] mypy_path`). This
  behaves exactly as mypy effectively did before the shadow existed
  (numpy resolved to `Any` after the parse failure), but without the
  fatal syntax error — so mypy now type-checks *every* server module.
* That surfaces ~700 **latent typing-debt errors** that were previously
  hidden behind the numpy parse failure. They are **tracked, not
  fixed**, via a count-based ratchet: `mypy-baseline.json` records the
  current per-error-code counts. The pre-push hook fails only if a
  count **grows**.
* **Never regenerate the baseline to add errors.** The ratchet only
  moves down. If you fix errors, regenerate to lock in the new (lower)
  floor.

## Why a ratchet?

The naive gate would be:

```bash
python -m mypy voice_typer/server/
```

That command exits non-zero today because of the ~700 baselined errors,
so it can never be a CI/pre-push gate until all of them are fixed —
fixing them all at once is out of scope. The ratchet converts the gate
from "zero errors" (impossible today) to "**no new errors**" (always
enforceable), mirroring the established
[ruff ratchet](ruff-ratchet.md) pattern.

## How it works

1. `scripts/mypy_ratchet_check.py` runs `python -m mypy
   voice_typer/server/`, reduces the output to `(total_count,
   by_code)` counts, and compares them against `mypy-baseline.json`.
2. Exit code 0 if every code count (and the total) is `<=` the
   baseline. Exit code 1 if any grew — the full mypy output is printed
   so the new errors are easy to locate.
3. Regenerate the baseline (only after FIXING errors):

   ```bash
   python scripts/mypy_ratchet_check.py --regenerate
   ```

   The script **refuses** to regenerate if the new total is *higher*
   than the old total — that would be a regression, not a ratchet.
   Pass `--force` only to bootstrap a missing baseline or for an
   emergency re-baselining after a deliberate scope change.

## Where it runs

* **Pre-push hook** — `.pre-commit-config.yaml` `mypy` hook (local,
  `language: system`, `stages: [pre-push]`): runs
  `python scripts/mypy_ratchet_check.py`. Blocks pushes that add new
  mypy errors.
* **`make typecheck`** — runs the ratchet script in parallel with
  TypeScript and ruff.

## Schema of `mypy-baseline.json`

* `total_count` — total number of mypy errors (non-negative int).
* `by_code` — map of mypy error code → count (e.g. `attr-defined`,
  `name-defined`, `arg-type`). Every key/value must be a
  non-negative int.
* Underscore-prefixed metadata fields (`_schema_version`, `_target`,
  `_updated`) are allowed and preserved on regeneration, but ignored
  by the comparison.

## Relationship to pyrefly

pyrefly keeps its own search path (`voice_typer/stubs`) and resolves
the *real* numpy through the active interpreter's site-packages at its
3.12 language level, so it type-checks the audio pipeline against the
real numpy stubs. The mypy shadow stub affects mypy only — stubs are
never imported at runtime.

## FAQ

**Why not bump `[tool.mypy] python_version` to 3.12?** That lets
3.11+-only typing constructs slip through mypy and break 3.10 installs
at runtime — the whole point of the documented 3.10 floor (see the
divergence matrix in `pyproject.toml`). The stub shadow keeps the floor
intact for the project's own code.

**Why not fix all ~700 errors?** They are pre-existing latent typing
debt across 130+ files (previously hidden behind the numpy parse
failure). Fixing them is a separate, large effort; the ratchet keeps
them tracked and non-blocking in the meantime.
