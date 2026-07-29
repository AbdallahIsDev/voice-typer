# Model Delete — Confirm-Only, No Undo (NEW-UX-004)

**Status**: Decided (2025 — d-review NEW-UX-004)
**Decision owner**: voice-typer UX
**Supersedes**: none
**Related code**:
- `voice_typer/client/src/renderer/src/pages/Models.tsx` — `confirmDeleteModel`
- `voice_typer/server/service.py` — `VoiceTyperService.delete_model`
- `voice_typer/server/handlers/model_handlers.py` — `_handle_delete_model`

## Context

Every other destructive action in the renderer offers a 6-second undo toast via
`showUndoableToast` (see `voice_typer/client/src/renderer/src/hooks/useSnackbar.ts`):

| Page            | Action            | Undo IPC            |
| --------------- | ----------------- | ------------------- |
| `History.tsx`   | delete entry      | `restore_history`   |
| `Templates.tsx` | delete template   | (re-create)         |
| `Vocabulary.tsx`| delete vocabulary | (re-add)            |

The d-review NEW-UX-004 flagged that model delete uses a **confirm dialog
only** (`ConfirmDialog` in `Models.tsx`) and never offers an undo toast — an
apparent inconsistency.

## Decision

**Model delete stays confirm-only. No undo toast.**

This is intentional. The two ways to implement undo on a model are both bad:

### Option A — Soft-delete (move model dir to trash dir for 6s, then hard-delete)

The "model directory" is the HuggingFace hub cache entry under
`~/.voice-typer/huggingface/hub/models--{repo_id}/`. Sizes:

| Model                          | Approx. size |
| ------------------------------ | ------------ |
| `tiny.en`, `base.en`           | ~75–150 MB   |
| `medium.en`                    | ~1.5 GB      |
| `large-v3`, `large-v3-turbo`   | ~1.5–3 GB    |
| `nvidia/parakeet-tdt-0.6b-v3`  | ~2.5 GB      |

For the popular large models, soft-delete would keep **1.5–3 GB on disk for the
whole undo window** — directly defeating the user's intent. The user deleted
the model **to free disk space**; holding the bytes hostage for 6 seconds (and
silently retaining them if the app crashes mid-window) is the wrong default.

### Option B — Re-download-as-undo (hard-delete now, Undo re-runs `download_model`)

`download_model` re-fetches from HuggingFace. On a typical home connection
(20 Mbit/s down) re-downloading 1.5 GB takes **~10 minutes**; on a throttled
or mobile connection it can take an hour and cost real money. An "Undo" button
that spins a download progress bar for 10 minutes does not feel like an undo —
it feels like a bug. Worse, the undo toast (6 s timeout) would dismiss long
before the download finished, leaving the user to discover via the model list
that the file is "still downloading".

## Why the confirm dialog is sufficient

The most common failure mode for a destructive action is the **accidental
click**, not the regretted click. The `ConfirmDialog` in `Models.tsx` (gated
by `deleteModelTarget`) already requires the user to read the model name and
press a separate "Delete" button — that catches the misclick case. Re-downloads
are a one-click recovery from the same model card (`downloadModel`), so a
user who really did change their mind has a clean, discoverable path back.

## When to revisit

If the project ever ships a **cloud model** with a small on-disk footprint
(e.g. a pointer-to-cloud-API entry under ~10 MB), soft-delete undo becomes
cheap enough to add per-model. The current `delete_model` IPC takes only the
model name, so a future `restore_model` IPC would need to know which delete
strategy (soft-trash vs. re-download) applies per model — that's not worth the
plumbing today.

## Test coverage

`tests/test_model_delete_ux.py` is a regression test that asserts:

1. The rationale comment block (marked `NEW-UX-004 (rationale)`) is present
   in `Models.tsx`, so a future refactor cannot silently swap the confirm
   dialog for an undo toast without updating the rationale.
2. This document (`docs/ux/model-delete-rationale.md`) exists, so the decision
   is discoverable from the repo root.
