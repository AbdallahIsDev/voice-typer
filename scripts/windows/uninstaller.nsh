; Voice Typer — NSIS uninstaller customization (CR-69 + CR-70).
;
; This file is `!include`d by electron-builder's generated installer.nsi
; (via the `nsis.include` config option in electron-builder.yml). It
; defines the `customUnInstall` macro that NSIS runs during the uninstall
; phase, AFTER the main app files are removed but BEFORE the installer
; exits. We use it to clean up per-user artifacts that survive the file
; removal:
;
;   CR-69: delete the HKCU Run key entries starting with "VoiceTyper"
;          (the per-user autostart entry written by
;          autostart_windows._register_app_autostart_runkey when the
;          user enables autostart in Settings). The Run key name format
;          is `VoiceTyper_<8char-hash>` (e.g. `VoiceTyper_a1b2c3d4`),
;          derived from SHA-256 of sys.executable. We delete ALL values
;          starting with "VoiceTyper" so stale entries from previous
;          installs (different install paths → different hashes) are
;          also cleaned up.
;
;          Also runs `schtasks /delete /tn "VoiceTyperAutostart*" /f`
;          for each matching Task Scheduler task — the fallback autostart
;          mechanism when the Run key fails. The Task Scheduler task
;          name format is `VoiceTyperAutostart_<8char-hash>`. We use
;          PowerShell's Get-ScheduledTask (which DOES support wildcards)
;          to enumerate matching tasks, then schtasks /Delete for each.
;
;   CR-70: remove the per-user data directory at %APPDATA%\voice-typer
;          (settings JSON, history DB, downloaded vocabularies, etc.).
;          Note: `deleteAppDataOnUninstall: true` in the `nsis:` block
;          of electron-builder.yml ALSO removes %APPDATA%\<productName>,
;          but we keep the explicit RMDir here as a belt-and-suspenders
;          guarantee (the appName may be renamed via `productName` while
;          our Python backend hardcodes `voice-typer` as the data dir
;          name — see voice_typer/server/_paths.py).
;
; HuggingFace cache (CR-70): the HF cache lives at
; %USERPROFILE%\.cache\huggingface on Windows. It can grow to multiple
; GB. We do NOT remove it by default — the user may want to reuse it
; for other HF-based apps. To remove it manually:
;     rmdir /s /q "%USERPROFILE%\.cache\huggingface"
;
; Reference: https://docs.electron.build/configuration/nsis#custom-hooks
;
; VALIDATE ON WINDOWS HOST:
;   1. Build the installer:
;         cd voice_typer/client && npm run build:win
;   2. Install the resulting *-setup.exe.
;   3. Launch Voice Typer → enable autostart via Settings.
;   4. Verify both autostart entries exist:
;         reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run | findstr VoiceTyper
;         schtasks /query /tn "VoiceTyperAutostart*" /v /fo LIST
;   5. Uninstall via "Add or remove programs".
;   6. Verify both autostart entries are gone:
;         reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run | findstr VoiceTyper
;            (Expected: no matches)
;         schtasks /query /tn "VoiceTyperAutostart*"
;            (Expected: ERROR: The system cannot find the file specified)
;   7. Verify the data dir is gone:
;         dir "%APPDATA%\voice-typer"
;            (Expected: File Not Found)

!macro customUnInstall
  ; ─── CR-69: HKCU Run key cleanup ────────────────────────────────────
  ; Enumerate HKCU\...\Run values + delete any whose name starts with
  ; "VoiceTyper" (10 chars). Covers both `VoiceTyper_<hash>` (Run key
  ; path) and `VoiceTyperAutostart_<hash>` (legacy task-name form).
  ;
  ; NSIS doesn't have a wildcard registry delete, so we iterate with
  ; EnumRegValue. When a value is deleted, the next value shifts into
  ; the current index slot, so we DON'T increment after deletion (we
  ; re-read the same index to get the next value). The loop terminates
  ; when EnumRegValue sets the error flag (no more values).
  Push $0    ; enum index
  Push $1    ; value name (current)
  Push $2    ; first-10-chars prefix
  StrCpy $0 0
  enum_loop:
    ClearErrors
    EnumRegValue $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $0
    IfErrors enum_done
    ; Copy first 10 chars of $1 into $2 and compare to "VoiceTyper".
    StrCpy $2 $1 10
    StrCmp $2 "VoiceTyper" 0 next_value
      DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $1
      DetailPrint "[voice-typer-uninstall] Removed HKCU Run key: $1"
      ; Don't increment — re-read same index (next value shifted in).
      Goto enum_loop
    next_value:
      IntOp $0 $0 + 1
      Goto enum_loop
  enum_done:
  Pop $2
  Pop $1
  Pop $0

  ; ─── CR-69: Task Scheduler task cleanup ─────────────────────────────
  ; `schtasks /Delete /TN "VoiceTyperAutostart*"` does NOT expand the
  ; wildcard reliably across Windows versions, so we use PowerShell's
  ; Get-ScheduledTask (which DOES support wildcards in -TaskName) to
  ; enumerate matching tasks, then call schtasks /Delete for each.
  ; Best-effort: failures (no matching task, PowerShell disabled, etc.)
  ; are non-fatal — the Pop discards the exit code.
  ;
  ; NSIS string escaping: $\" is a literal double-quote. We need them
  ; around the task name so schtasks handles names with spaces correctly
  ; (unlikely for "VoiceTyperAutostart_<hash>" but defensive).
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-ScheduledTask -TaskName VoiceTyperAutostart* -ErrorAction SilentlyContinue | ForEach-Object { schtasks.exe /Delete /TN $\"$($_.TaskName)$\" /F }"'
  Pop $0  ; exit code — best-effort, discard

  ; ─── CR-70: per-user data directory cleanup ────────────────────────
  ; Belt-and-suspenders: deleteAppDataOnUninstall: true in the nsis:
  ; block of electron-builder.yml also removes %APPDATA%\<productName>,
  ; but productName may be "Voice Typer" (with a space) while the Python
  ; backend uses "voice-typer" (hyphenated, lowercase) as the data dir
  ; name. We explicitly remove the latter to guarantee the data dir is
  ; purged regardless of productName / data-dir name drift.
  RMDir /r "$APPDATA\voice-typer"
  DetailPrint "[voice-typer-uninstall] Removed user data directory: $APPDATA\voice-typer"
!macroend
