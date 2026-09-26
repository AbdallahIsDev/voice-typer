# ADR-0024 Step 7 — slim-build gate DRAFT (DO NOT APPLY)

> DRAFT ONLY. Applying any of this before Step 6 verifies on a real
> machine reproduces the ADR trap: the app still imports ML in-process,
> so excluding the libs = `ModuleNotFoundError` on launch / silent VAD
> death. Flip to real changes only after the Step 6 sign-off exists.

## Grep gate (zero ML imports in the slim-core closure)

Slim-core closure = `voice_typer/server` minus the pack-owned
subtrees (engines + VAD + filters move to `voice_typer/worker` or
behind the worker hop first). Gate sketch (CI, after the sidecar
build, failing the job):

```bash
# Slim-core must not import ML runtimes in-process. torch/transformers
# already scan clean (removed Phase 1d); the live hits are onnxruntime
# (vad.py, asr_utils.py, gtcrn_backend.py, parakeet/_load.py,
# qwen_onnx_model.py, resource_probe.py), ctranslate2
# (transcription_device.py, transcription_fallback.py), faster_whisper
# (transcription.py, qwen_onnx_model.py).
grep -rEn "import onnxruntime|import ctranslate2|from faster_whisper|import faster_whisper|import torch|import transformers" \
  voice_typer/server --include='*.py' \
  | grep -v 'voice_typer/server/prewarm/' && exit 1
echo "Slim core is ML-import-free."
```

The exact include/exclude list must be recomputed at apply time
against the post-handoff tree (files move during Steps 3-4).

## Size gate (sketch against current Write-Host lines)

Anchor: `.github/workflows/tauri-windows-build.yml:508-510`
(`Write-Host "NSIS: …" / "MSI: …" / "Standalone: …"` — observability
only today). Insert AFTER the artifacts step, BEFORE signing, per
plan §11.4:

```yaml
- name: Assert sidecar size ≤ 185 MB (plan §11.4 — enforce post-handoff)
  shell: pwsh
  run: |
    $size = (Get-Item python-sidecar-<triple>.exe).Length / 1MB
    Write-Host "Sidecar size: $([math]::Round($size,2)) MB"
    if ($size -gt 185) { Write-Error "Sidecar exceeds 185 MB"; exit 1 }
```

Companion: tighten the existing informational ≤45 MB installer gate
(`tauri-windows-build.yml:554`, `continue-on-error: true` today) to
enforcing in the same commit.

## Files to touch (at apply time, same commit)

1. `.github/workflows/tauri-windows-build.yml` — add both gates;
   extend the signing foreach with the worker exe (5th binary,
   C-CI-11 notes the 4-binary enumeration).
2. `scripts/build/build_sidecar_{windows,macos,linux}.sh` + the inline
   Nuitka invocation in the workflow — add the ML
   `--nofollow-import-to` exclusions (NEVER before Step 6).
3. `tests/tauri/test_config_script_drift.py::
   TestNuitkaSidecarBuildsDoNotExcludeTorchDistributed` — this class
   HARD-FORBIDS the exclusions today (plan §11.2); update/delete it in
   the same commit or CI fails by design.
4. `pyproject.toml` / `requirements-lock.txt` — move ML deps to the
   worker/pack dependency set.
5. Ratchet baselines (`coverage/mypy/pyrefly/ruff`, plan §11.7).

## Apply precondition

Step 6 sign-off (log excerpts in the checklist) + `worker_client.py`
round-trip green. Until then this file stays a draft.
