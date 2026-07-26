# Releasing Voice Typer

This document describes the release process for Voice Typer. The project
ships per-platform installers (Windows `.exe`, macOS `.dmg`, Linux
`.deb` / `.rpm` / `.AppImage`) built by GitHub Actions on every `v*`
tag push.

> **TL;DR** — bump the version in `pyproject.toml` +
> `voice_typer/client/package.json` (use `scripts/build/sync_versions.py`),
> update `CHANGELOG.md`, tag with `vX.Y.Z`, push the tag. CI does the
> rest.

---

## 1. Versioning

Voice Typer follows [Semantic Versioning](https://semver.org/):

- **MAJOR** — incompatible API / IPC protocol changes (e.g. a
  `_COMMAND_REGISTRY` rename or removal that breaks older renderer
  builds).
- **MINOR** — new backwards-compatible features (new IPC command, new
  ASR backend, new UI page).
- **PATCH** — bug fixes, doc updates, dependency bumps with no behavior
  change.

The version lives in two places that **must stay in sync**:

| File | Field | Notes |
|------|-------|-------|
| `pyproject.toml` | `version` | PEP 621 — the Python package version. |
| `voice_typer/client/package.json` | `version` | The Electron app version (used by electron-builder for the NSIS / DMG / deb / rpm metadata). |

Use `scripts/build/sync_versions.py` to keep the version in sync. The
workflow is: **edit `pyproject.toml`** (the single source of truth)
**then run `--apply`** to propagate the new version to every other
file (`voice_typer/__init__.py`, `voice_typer/client/package.json`,
`voice_typer/client/electron-builder.yml`, `src-tauri/tauri.conf.json`,
`src-tauri/Cargo.toml`).

```bash
# 1. Edit pyproject.toml and bump "version" under [project].
# 2. Propagate the new version to every synced file:
python scripts/build/sync_versions.py --apply
git diff pyproject.toml voice_typer/client/package.json src-tauri/Cargo.toml
```

The Tauri host (`src-tauri/Cargo.toml` + `src-tauri/tauri.conf.json`)
tracks the same version. `sync_versions.py` syncs the Tauri files by
default (see `--apply` in `scripts/build/sync_versions.py --help`);
the Tauri stack is additive during the migration (see ADR-0020) so
releases ship from the Electron stack until cutover.

## 2. CHANGELOG

Every release updates `CHANGELOG.md`:

1. Promote the `## [Unreleased]` section to `## [X.Y.Z] - YYYY-MM-DD`.
2. Add a fresh `## [Unreleased] - TBD` heading above it for the next
   cycle's entries.
3. The "User-Facing Changes" and "Developer-Facing Changes" subsections
   stay — they're a permanent record, not a queue.

Keep `Keep a Changelog` formatting (the file header links to the spec).

## 3. Pre-release checklist

Run this on `main` (or your release branch) **before** tagging:

```bash
# 1. Full Python suite + coverage gate (65% — see pyproject.toml).
pytest tests/ -v

# 2. Frontend suite + lint + typecheck + production build.
cd voice_typer/client
npm run test && npm run lint && npm run typecheck && npm run build
cd ../..

# 3. Pre-commit hooks (ruff, biome, mypy, pyrefly, etc.).
pre-commit run --all-files

# 4. Verify versions are in sync (CI mode — exits 1 on drift).
python scripts/build/sync_versions.py --check

# 5. Verify the git tree is clean.
git status --porcelain
```

If any of the above fail, **stop** — do not tag a release from a dirty
or red tree.

## 4. Tagging

```bash
# 1. Edit pyproject.toml and bump "version" under [project] to 1.2.0.
# 2. Propagate the new version to package.json, Cargo.toml, tauri.conf.json, etc.
python scripts/build/sync_versions.py --apply

# Commit the bump.
git add pyproject.toml voice_typer/client/package.json src-tauri/Cargo.toml src-tauri/tauri.conf.json
git commit -m "chore(release): bump to 1.2.0"

# Update CHANGELOG.md (promote [Unreleased] → [1.2.0]).
git add CHANGELOG.md
git commit -m "docs(changelog): 1.2.0 release notes"

# Tag.
git tag -a v1.2.0 -m "Release 1.2.0"
git push origin main
git push origin v1.2.0
```

The tag push triggers `.github/workflows/build.yml` which:

1. Runs the full test suite on Windows / macOS / Linux.
2. Builds native hotkey binaries (Swift on macOS, C on Windows, C on Linux).
3. Builds the Electron installer per platform (NSIS / DMG / deb / rpm / AppImage).
4. Generates SHA-256 checksums via `scripts/generate_checksums.py`.
5. Uploads artifacts to the GitHub Release created from the tag.

The Tauri build matrices (`.github/workflows/tauri-{windows,macos,linux}-build.yml`)
also fire on tag pushes; their artifacts are uploaded to the same
release but tagged `tauri-` until cutover (ADR-0020).

## 5. Release notes

The GitHub Release description should be a copy-paste of the
`## [X.Y.Z]` section from `CHANGELOG.md`, plus a "Known issues" list
if any. Use the GitHub web UI (or `gh release edit`) to attach the
release notes — the workflow does not auto-populate the body.

## 6. Post-release

1. **Bump to the next dev version** if the next cycle is starting
   immediately: edit `pyproject.toml` to `1.3.0.dev0`, then run
   `python scripts/build/sync_versions.py --apply`.
2. **Open a tracking issue** for any known issues discovered during the
   release (tag them with the release milestone).
3. **Watch the CI matrix** for the first 30 minutes after tag push —
   cross-platform build failures are most common on the first release
   after a native-binary or signing change.

## 7. Hotfixes

For a hotfix against an already-released version:

1. Branch off the tag: `git checkout -b hotfix/1.2.1 v1.2.0`.
2. Apply the minimal fix + a regression test.
3. Bump the patch version: edit `pyproject.toml` to `1.2.1`, then run
   `python scripts/build/sync_versions.py --apply`.
4. Update `CHANGELOG.md` under a new `## [1.2.1]` heading.
5. Commit, tag `v1.2.1`, push the tag.
6. Cherry-pick the fix + CHANGELOG entry back to `main`.

## 8. Rollback

If a release ships with a critical regression:

1. **Do not delete the tag** — it's referenced by the GitHub Release
   and by users who pinned to it. Delete the Release artifacts if
   needed but leave the tag for auditability.
2. Publish a new patch release (`vX.Y.Z+1`) with the fix.
3. Mark the broken version as "Yanked" in the GitHub Release notes.
4. If the broken build is still the "latest" download, edit the
   Release to point at the previous tag's assets while the fix is in
   flight.

## 9. CI gates

The following CI jobs must be green for a release to ship:

- `build.yml` — full pytest + vitest + lint + typecheck + installer
  build on all three platforms.
- `codeql.yml` — static security analysis.
- `client-ci.yml` — frontend-only lint / typecheck / test.
- `populate-hashes.yml` — model hash manifest regeneration (run only
  when model files change).
- `tauri-*-build.yml` — Tauri stack builds (additive during ADR-0020
  migration; failures here do not block an Electron release but should
  be investigated before cutover).

The husky `pre-push` hook (`.husky/pre-push`) runs `pytest tests/ -x`
before any push — do not bypass it for release pushes.

## 10. Signing

Code signing is configured but **not yet operational** (see
`FEATURES.md` → "Distribution — gaps that remain"). Until signing is
wired up, users will see SmartScreen / Gatekeeper / `apt-get` warnings
on first install. Document the workaround in the release notes:

- **Windows**: "More info → Run anyway" on the SmartScreen dialog.
- **macOS**: `xattr -dr com.apple.quarantine /Applications/Voice\ Typer.app`
  after dragging to `/Applications`.
- **Linux**: `sudo dpkg -i --force-unsatisfied-deps` if dependency
  warnings appear (the postinst script installs them via `apt-get`).

When signing is added (planned post-1.0), update this section with the
certificate / notarization workflow and remove the workarounds.
