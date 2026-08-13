; Voice Typer — NSIS installer-time hooks (slim-core / runtime-pack split).
;
; Companion to ``scripts/windows/uninstaller.nsh`` (which defines the
; ``customUnInstall`` macro for post-uninstall cleanup). This file defines
; the INSTALL-time hooks the slim-core NSIS installer runs:
;
;   1. A custom Components-page Section ``"Include offline engine pack"``
;      with a checkbox (default: selected). Tauri v2's
;      ``bundle.windows.nsis`` config has NO first-class checkbox option
;      (verified against the schema at https://schema.tauri.app/config/2 —
;      see plan-runtime-pack-split.md §9). The Components page is the
;      NSIS-native way to surface a per-feature checkbox; an optional
;      ``Section`` (one WITHOUT ``SectionIn RO``) renders as a checkbox
;      the user can tick/untick. The pack itself is NEVER bundled into
;      the slim-core installer (it would bloat the installer from ~35 MB
;      to ~215 MB — see plan-runtime-pack-split.md §5.3). Instead the
;      checkbox state is persisted to ``installer-state.json`` and the
;      slim-core app reads it on first launch to decide whether to start
;      the silent background pack download (plan §4.8 consent gate, §8.4
;      consent-gate correction).
;
;   2. The ``customInstall`` macro Tauri v2 invokes AFTER the main app
;      files are written but BEFORE the installer exits. It writes
;      ``%LOCALAPPDATA%\voice-typer\installer-state.json`` with the
;      consent value the user picked on the Components page. The slim-core
;      Python backend reads this file at first launch via
;      ``voice_typer/server/installer_state.py`` (owned by Sub-agent 3 —
;      see plan-runtime-pack-split.md §4.8). Schema (pinned by tests in
;      ``tests/tauri/test_installer_naming.py``):
;
;          {
;            "include_offline_engine_pack": <bool>,
;            "installer_version": "<PRODUCT_VERSION>",
;            "pack_bundled": false
;          }
;
;      ``pack_bundled`` is false for the slim-core installer; the
;      full-offline installer template (``scripts/windows/full-offline-installer.nsi``)
;      sets it true when it bundles the pack zip in-tree.
;
; Reference: https://docs.tauri.app/reference/config/#nsisconfiguration
; Tauri v2 ``!include``s every ``installerHooks`` entry into the generated
; ``installer.nsi``; the macros below are invoked at the documented hook
; points (``customInstall`` runs in the ``-post`` Section after the main
; install).
;
; VALIDATE ON WINDOWS HOST:
;   1. Build the slim-core installer:
;         cd src-tauri && cargo tauri build --config tauri.windows-x86_64.conf.json
;   2. Run the resulting ``voice-typer-<version>-x64-setup.exe``.
;   3. On the Components page, confirm the "Include offline engine pack"
;      checkbox appears and is ticked by default.
;   4. Untick it, finish the install.
;   5. Verify the state file was written with the consent value false:
;         type "%LOCALAPPDATA%\voice-typer\installer-state.json"
;         (Expected: {"include_offline_engine_pack": false, ...})
;   6. Repeat with the checkbox ticked — confirm the value is true.

; ─── Section variable ─────────────────────────────────────────────────────
; NSIS ``Var`` declarations are file-scoped — declaring here is safe even
; though ``installer.nsi`` (Tauri-generated) declares its own. The Var is
; initialised to "0" so an unticked section body never runs and the value
; stays "0" (the ``customInstall`` macro reads it to decide what to write).
Var IncludeOfflineEnginePack

; ─── Components-page Section ──────────────────────────────────────────────
; A ``Section`` WITHOUT ``SectionIn RO`` is OPTIONAL: the NSIS Components
; page renders it as a checkbox the user can untick. The section body
; runs ONLY when the checkbox is ticked at install time (the standard
; NSIS contract — see NSIS docs §4.5 "Sections"). Default state is
; "selected" (checkbox ticked) because the plan §4.8 default is
; auto-download — the user opts OUT, not IN.
;
; The section is EMPTY of file operations: the pack is downloaded at
; first launch, not at install time (plan §4.8). The body's only job is
; to flip ``$IncludeOfflineEnginePack`` to "1" so the ``customInstall``
; macro knows the user consented.
Section "Include offline engine pack" SecIncludePack
  ; Default-selected: the Components page checkbox starts ticked.
  ; (NSIS sections are selected by default unless ``SectionIn RO`` is
  ; called inside the section body — we deliberately do NOT call it.)
  StrCpy $IncludeOfflineEnginePack "1"
  DetailPrint "[voice-typer-installer] User selected: include offline engine pack (download starts on first launch)."
SectionEnd

; Human-readable description shown under the Components page list. NSIS
; ``LangString`` lets us localize later (plan §9.3 adds 8 locale strings
; for the pack UI; this description is one of them). The LangString MUST
; be declared inside a SectionGroup or at the top level — top-level is
; fine here.
LangString DESC_SecIncludePack ${LANG_ENGLISH} \
  "Downloads the offline ASR engine pack (~180 MB) on first launch. Cloud transcription works without it."

; ─── customInstall macro ─────────────────────────────────────────────────
; Tauri v2 invokes ``customInstall`` in the ``-post`` Section of the
; generated ``installer.nsi`` — AFTER the main app files are written,
; BEFORE the installer exits. We use it to persist the user's checkbox
; choice to ``installer-state.json`` so the slim-core Python backend can
; read it on first launch (plan §4.8 consent gate).
;
; The state file lives at ``%LOCALAPPDATA%\voice-typer\installer-state.json``
; — the SAME per-user data root the Python backend uses for the runtime
; pack (plan §4.7). ``%LOCALAPPDATA%`` expands to ``$LOCALAPPDATA`` in
; NSIS. ``CreateDirectory`` is idempotent (no error if the dir exists).
;
; JSON shape pinned by ``tests/tauri/test_installer_naming.py`` (the
; ``installer_state.json`` schema test). Keep the field names EXACT —
; the Python reader is a strict schema check, not a tolerant parser.
!macro customInstall
  ; Belt-and-suspenders: default to "0" if the Section body somehow
  ; didn't run (e.g. the user is running the installer with /S silent
  ; mode and the Components page was skipped — NSIS selects all
  ; optional sections by default in silent mode, but defensive coding
  ; is cheap here).
  StrCpy $IncludeOfflineEnginePack "0"
  SectionGetFlags ${SecIncludePack} $0
  IntOp $0 $0 & ${SF_SELECTED}
  ${If} $0 == ${SF_SELECTED}
    StrCpy $IncludeOfflineEnginePack "1"
  ${EndIf}

  CreateDirectory "$LOCALAPPDATA\voice-typer"
  ClearErrors
  FileOpen $0 "$LOCALAPPDATA\voice-typer\installer-state.json" w
  IfErrors installer_state_done
  ${If} $IncludeOfflineEnginePack == "1"
    FileWrite $0 `{"include_offline_engine_pack": true, "installer_version": "${PRODUCT_VERSION}", "pack_bundled": false}`$\r$\n`
  ${Else}
    FileWrite $0 `{"include_offline_engine_pack": false, "installer_version": "${PRODUCT_VERSION}", "pack_bundled": false}`$\r$\n`
  ${EndIf}
  FileClose $0
  DetailPrint "[voice-typer-installer] Wrote installer-state.json (include_offline_engine_pack=$IncludeOfflineEnginePack)."
  installer_state_done:
!macroend
