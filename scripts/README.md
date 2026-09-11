# scripts/

Tooling for the project. The canonical dev loop (env setup, tests, lint,
typecheck) lives in `CONTRIBUTING.md` and `AGENTS.md` at the repo root —
this file only maps what is in the tree so a contributor can tell live
tooling from session residue.

Subdirectories are self-documenting: `build/` (per-platform build +
release scripts), `release/` (GitHub Releases publisher), `linux/`,
`macos/`, `windows/` (platform install/uninstall), `ci/` (CI helpers).

## Live tooling (referenced by CI, Makefile, pre-commit, or docs)

| Script | Purpose |
| --- | --- |
| `run_bench.py` | Run every `bench/bench_*.py` with `--json`, concatenate into `bench-current.json` (backing `make bench`). |
| `check_branding.py` | CI gate: no hardcoded app-name literals outside the branding source-of-truth files. |
| `gen_tauri_icons_stub.py` | Generate/check the fake Tauri sidecar binary stubs a dev build needs. |
| `check_linux_scripts_lf.py` | Verify Linux install scripts keep LF line endings. |
| `check_new_command.sh` | Guard for new IPC command wiring. |
| `generate_checksums.py` | SHA-256 checksums for release artifacts. |
| `diagnostics.py` | Run repo diagnostics suites outside pytest. |
| `ruff_ratchet_check.py` | Ruff error-count ratchet gate (vs `ruff-baseline.json`). |
| `mypy_ratchet_check.py` | Mypy error-count ratchet gate (vs `mypy-baseline.json`). |
| `coverage_ratchet_check.py` | Coverage floor ratchet gate (vs `coverage-baseline.json`). |
| `_ratchet_common.py` | Shared skeleton for the three ratchet scripts. |
| `add_i18n_keys.py` / `backfill_i18n_keys.py` / `apply_translations.py` | Locale-file maintenance (add keys, backfill, apply translations). |
| `_i18n_common.py` | Shared helpers for the i18n maintenance scripts. |
| `populate_model_hashes.py` | Regenerate the model integrity hash manifest. |
| `profile_imports.py` | Coldstart import-time profiling report. |
| `pre-commit-runner.cjs` | Local pre-commit hook runner. |
| `tauri-sign.cmd` | Windows signing helper. |

## Session-tier tooling (live but not wired into CI / Makefile)

One-off drivers kept for the workflows they document. They hardcode
sandbox paths or session-specific assumptions, read before reuse.

| Script | Purpose |
| --- | --- |
| `chunk_gate_driver.py` | Drives the domain-chunked full-suite protocol (writes to `/tmp/chunk_gate`, uses the repo's `.venv`). |
| `package_changes.py` | Build `changes.zip` from `git status --porcelain` (cloud-session handoff). |
| `build_changes_zip.py` | Older `changes.zip` builder (diff vs HEAD + untracked files). |
| `regenerate_pyrefly_baseline.py` | Regenerate `pyrefly-baseline.json` from `pyrefly-current.json`, only after a verified drop in error count. |
| `gen_caption_glyph_paths.py` | Extract native Windows caption-button glyph SVG paths for the custom title bar. |
