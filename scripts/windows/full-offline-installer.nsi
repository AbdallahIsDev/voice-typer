; Voice Typer — Full-offline NSIS installer template (slim core + pack bundled).
;
; This is a STANDALONE NSIS script (NOT a Tauri-generated installer.nsi).
; It produces a SECOND installer artifact alongside the slim-core installer
; (plan-runtime-pack-split.md §4.1 / §11.9 / open-decision §14.5: "always
; publish both"). The slim-core installer is the default download; this
; full-offline installer exists for offline-install scenarios (air-gapped
; corp networks, slow links, pre-staged SCCM deploys, etc.).
;
; Build: see ``scripts/build/build_full_offline_installer_windows.sh``.
; The build script invokes ``makensis`` on this file with these !defines:
;
;   SLIM_CORE_EXE   = absolute path to the slim-core installer .exe
;                     produced by ``cargo tauri build --config
;                     tauri.windows-x86_64.conf.json`` (or aarch64 sibling).
;   PACK_ZIP        = absolute path to the runtime-pack zip
;                     (``voice-typer-runtime-pack-<ver>-<triple>.zip``)
;                     produced by Sub-agent 5's worker build script.
;   PACK_VERSION    = pack version string (e.g. "3").
;   APP_VERSION     = slim-core app version (matches tauri.conf.json).
;   PRODUCT_TRIPLE = target triple (e.g. "x86_64-pc-windows-msvc").
;
; Output: ``voice-typer-full-offline-<APP_VERSION>-<PRODUCT_TRIPLE>.exe``
; (a NEW artifact name per §11.9; the existing slim-core
; ``voice-typer-<version>-<triple>.exe`` name is left UNCHANGED — C-CI-13
; forbids renaming existing artifacts).
;
; What this installer does at install time:
;   1. Extracts the bundled pack zip to
;      ``%LOCALAPPDATA%\voice-typer\runtime-pack\<PACK_VERSION>\``.
;      This is the SAME per-user path the slim-core app's runtime-pack
;      resolver looks at (plan §4.7 — ``src-tauri/src/platform/worker_path.rs``).
;   2. Writes ``%LOCALAPPDATA%\voice-typer\installer-state.json`` with
;      ``pack_bundled: true`` so the slim-core app's first-launch
;      consent gate (plan §4.8) SKIPS the silent background download —
;      the pack is already on disk.
;   3. Runs the bundled slim-core installer .exe with the same NSIS args
;      the user passed to this wrapper (so silent installs propagate:
;      ``/S`` runs the slim-core installer silently).
;
; The wrapper itself has NO Components-page checkbox — the pack IS
; bundled, so the "Include offline engine pack" choice the slim-core
; installer exposes is moot here. We always install the pack to disk.
; The slim-core installer's own Components page still appears (we run
; it after unpacking the pack); the user can untick the checkbox there
; but the pack is already extracted — the checkbox only governs the
; silent *download*, which is skipped because ``pack_bundled: true``.

!ifndef SLIM_CORE_EXE
  !error "SLIM_CORE_EXE must be defined (path to slim-core installer .exe). Build via scripts/build/build_full_offline_installer_windows.sh."
!endif
!ifndef PACK_ZIP
  !error "PACK_ZIP must be defined (path to voice-typer-runtime-pack-<ver>-<triple>.zip). Build via Sub-agent 5's worker build script."
!endif
!ifndef PACK_VERSION
  !error "PACK_VERSION must be defined (e.g. '3')."
!endif
!ifndef APP_VERSION
  !error "APP_VERSION must be defined (slim-core app version, matches tauri.conf.json)."
!endif
!ifndef PRODUCT_TRIPLE
  !error "PRODUCT_TRIPLE must be defined (e.g. 'x86_64-pc-windows-msvc')."
!endif

Unicode true
ManifestDPIAware true
SetCompressor /SOLID lzma

Name "Voice Typer ${APP_VERSION} (full offline)"
OutFile "voice-typer-full-offline-${APP_VERSION}-${PRODUCT_TRIPLE}.exe"
InstallDir "$LOCALAPPDATA\voice-typer"
RequestExecutionLevel user
ShowInstDetails show

; ─── Version metadata (shown in installer .exe Properties → Details) ─────
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "Voice Typer (full offline installer)"
VIAddVersionKey "FileDescription" "Slim core + offline engine pack bundled"
VIAddVersionKey "CompanyName" "Voice Typer"
VIAddVersionKey "LegalCopyright" "(c) Voice Typer contributors"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"

; ─── Pack extraction section ─────────────────────────────────────────────
; The pack is extracted to a versioned subdirectory of the per-user
; data root (plan §4.7: %LOCALAPPDATA%\voice-typer\runtime-pack\<version>\).
; The slim-core app's runtime-pack resolver discovers the latest version
; by scanning this directory.
Section "Offline engine pack (bundled)" SecPack
  SectionIn RO  ; always installed — pack is the entire point of this artifact
  SetOutPath "$LOCALAPPDATA\voice-typer\runtime-pack\${PACK_VERSION}"
  DetailPrint "[voice-typer-full-offline] Extracting pack v${PACK_VERSION} to $LOCALAPPDATA\voice-typer\runtime-pack\${PACK_VERSION}\..."
  File "${PACK_ZIP}"
  ; Unzip via the bundled Python interpreter? No — we don't have one yet
  ; at install time (the slim-core installer hasn't run). Use NSIS's
  ; native unzip via the ``unzip`` plugin OR shell out to PowerShell's
  ; ``Expand-Archive`` (built-in on Windows 10+). PowerShell is the
  ; zero-dependency choice.
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $\'$LOCALAPPDATA\voice-typer\runtime-pack\${PACK_VERSION}\voice-typer-runtime-pack-${PACK_VERSION}-${PRODUCT_TRIPLE}.zip$\' -DestinationPath $\'$LOCALAPPDATA\voice-typer\runtime-pack\${PACK_VERSION}$\' -Force"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "[voice-typer-full-offline] WARNING: Expand-Archive exit code $0 — pack may be partially extracted."
  ${EndIf}
  ; Remove the zip after extraction (saves ~180 MB on disk).
  Delete "$LOCALAPPDATA\voice-typer\runtime-pack\${PACK_VERSION}\voice-typer-runtime-pack-${PACK_VERSION}-${PRODUCT_TRIPLE}.zip"
SectionEnd

; ─── installer-state.json write section ─────────────────────────────────
; Signals to the slim-core app (read at first launch via
; ``voice_typer/server/installer_state.py``) that:
;   - The pack was bundled into this installer (``pack_bundled: true``).
;   - The user's "Include offline engine pack" checkbox choice from the
;     slim-core installer (which runs next) governs the silent
;     download — but since the pack is already on disk, the slim-core
;     app's pack resolver finds it and skips the download regardless.
; We still write ``include_offline_engine_pack: true`` because the user
; chose to install the full-offline variant (consent is implicit).
Section "Write installer state" SecState
  SectionIn RO
  CreateDirectory "$LOCALAPPDATA\voice-typer"
  ClearErrors
  FileOpen $0 "$LOCALAPPDATA\voice-typer\installer-state.json" w
  IfErrors state_done
  FileWrite $0 `{"include_offline_engine_pack": true, "installer_version": "${APP_VERSION}", "pack_bundled": true, "pack_version": "${PACK_VERSION}"}`$\r$\n`
  FileClose $0
  DetailPrint "[voice-typer-full-offline] Wrote installer-state.json (pack_bundled=true, pack_version=${PACK_VERSION})."
  state_done:
SectionEnd

; ─── Slim-core installer launch section ─────────────────────────────────
; Run the bundled slim-core installer .exe. NSIS args the user passed to
; THIS wrapper are forwarded via ``$CMDLINE`` (so ``/S`` silent installs
; propagate). The slim-core installer is invoked with ``ExecWait`` so
; this wrapper does NOT exit until the slim-core install completes
; (otherwise the user sees the wrapper finish before the actual app
; install — confusing UX).
Section "Install slim core" SecSlimCore
  SectionIn RO
  SetOutPath "$PLUGINSDIR"
  ; ``File /oname=`` extracts the source file under a fixed name so the
  ; ExecWait line below doesn't have to know the source filename.
  File /oname=slim-core-setup.exe "${SLIM_CORE_EXE}"
  DetailPrint "[voice-typer-full-offline] Launching slim-core installer..."
  ; Forward the original cmdline so /S and /D=path propagate.
  ExecWait '"$PLUGINSDIR\slim-core-setup.exe" $CMDLINE' $0
  ${If} $0 != 0
    DetailPrint "[voice-typer-full-offline] WARNING: slim-core installer exit code $0."
  ${EndIf}
SectionEnd

; ─── customInstall: no-op (handled by the sections above) ───────────────
; This macro is invoked by Tauri v2's installer.nsi; we never run that
; template here (this IS a custom template), but defining the macro
; keeps the file syntactically valid if it's ever ``!include``d by
; another script.
!macro customInstall
!macroend
