; Lausu. NSIS uninstaller customization (CR-69 + CR-70).
;
; This file is `!include`d by Tauri v2's NSIS bundler ONLY
; (src-tauri/tauri.conf.json -> bundle.windows.nsis.installerHooks, must
; be an .nsh here, NOT the .bat: NSIS cannot `!include` a batch file).
; The former builder `nsis.include` wiring is gone with the
; previous host (removed 2026-09-17). It defines the
; `customUnInstall` macro that NSIS runs during the uninstall phase,
; AFTER the main app files are removed but BEFORE the installer exits.
; We use it to clean up per-user artifacts that survive the file
; removal:
;
;   CR-69: delete the HKCU Run key entries owned by Lausu: the
;          current canonical names starting with "com.Lausu" (the
;          per-user autostart entry written by
;          autostart_windows._register_app_autostart_runkey when the
;          user enables autostart in Settings, format
;          `com.Lausu.autostart_<8char-hash>`, reverse-DNS
;          namespace) AND the pre-rename bare names starting with
;          "Lausu" (format `Lausu_<8char-hash>`, e.g.
;          `Lausu_a1b2c3d4`). We delete ALL such values so stale
;          entries from previous installs (different install paths →
;          different hashes) are also cleaned up.
;
;          Also runs `schtasks /delete /tn "LausuAutostart*" /f`
;          for each matching Task Scheduler task, the fallback autostart
;          mechanism when the Run key fails. The Task Scheduler task
;          name format is `com.Lausu.autostart_<8char-hash>`
;          (pre-rename: `LausuAutostart_<8char-hash>`). We use
;          PowerShell's Get-ScheduledTask (which DOES support wildcards)
;          to enumerate matching tasks, then schtasks /Delete for each.
;          The wildcard union also catches the prewarm task
;          `com.Lausu.prewarm` / legacy `LausuPrewarm`.
;
;   CR-70: remove the per-user data directory at %APPDATA%\lausu
;          (settings JSON, history DB, downloaded vocabularies, etc.).
;          Note: Tauri NSIS may also remove the product data dir via
;          its own uninstall config, but we keep the explicit RMDir
;          here as a belt-and-suspenders guarantee (the appName may be
;          renamed via `productName` while our Python backend hardcodes
;          `lausu` as the data dir name: see
;          voice_typer/server/_paths.py).
;
; HuggingFace cache (CR-70): the HF cache lives at
; %USERPROFILE%\.cache\huggingface on Windows. It can grow to multiple
; GB. We do NOT remove it by default, the user may want to reuse it
; for other HF-based apps. To remove it manually:
;     rmdir /s /q "%USERPROFILE%\.cache\huggingface"
;
; VALIDATE ON WINDOWS HOST:
;   1. Build the installer via the Tauri workflow / `cargo tauri build`.
;   2. Install the resulting *-setup.exe.
;   3. Launch Lausu → enable autostart via Settings.
;   4. Verify both autostart entries exist:
;         reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run | findstr Lausu
;         schtasks /query /tn "LausuAutostart*" /v /fo LIST
;   5. Uninstall via "Add or remove programs".
;   6. Verify both autostart entries are gone:
;         reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run | findstr Lausu
;            (Expected: no matches)
;         schtasks /query /tn "LausuAutostart*"
;            (Expected: ERROR: The system cannot find the file specified)
;   7. Verify the data dir is gone:
;         dir "%APPDATA%\lausu"
;            (Expected: File Not Found)

!macro customUnInstall
  ; ─── CR-69: HKCU Run key cleanup ────────────────────────────────────
  ; Enumerate HKCU\...\Run values + delete any owned by Lausu:
  ; current canonical names starting with "com.Lausu" (13 chars,
  ; e.g. `com.Lausu.autostart_<hash>` and the prewarm Run-key
  ; value `com.Lausu.prewarm`) AND pre-rename bare names starting
  ; with "Lausu" (10 chars, e.g. `Lausu_<hash>` /
  ; `LausuAutostart_<hash>`).
  ;
  ; NSIS doesn't have a wildcard registry delete, so we iterate with
  ; EnumRegValue. When a value is deleted, the next value shifts into
  ; the current index slot, so we DON'T increment after deletion (we
  ; re-read the same index to get the next value). The loop terminates
  ; when EnumRegValue sets the error flag (no more values).
  Push $0    ; enum index
  Push $1    ; value name (current)
  Push $2    ; prefix (current value name, truncated)
  StrCpy $0 0
  enum_loop:
    ClearErrors
    EnumRegValue $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $0
    IfErrors enum_done
    ; Copy first 13 chars of $1 into $2 and compare to "com.Lausu".
    StrCpy $2 $1 13
    StrCmp $2 "com.Lausu" 0 try_legacy_prefix
      DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $1
      DetailPrint "[lausu-uninstall] Removed HKCU Run key: $1"
      ; Don't increment, re-read same index (next value shifted in).
      Goto enum_loop
    ; Copy first 10 chars of $1 into $2 and compare to "Lausu"
    ; (pre-rename bare scheme).
    try_legacy_prefix:
    StrCpy $2 $1 10
    StrCmp $2 "Lausu" 0 next_value
      DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $1
      DetailPrint "[lausu-uninstall] Removed HKCU Run key: $1"
      ; Don't increment, re-read same index (next value shifted in).
      Goto enum_loop
    next_value:
      IntOp $0 $0 + 1
      Goto enum_loop
  enum_done:
  Pop $2
  Pop $1
  Pop $0

  ; ─── CR-69: Task Scheduler task cleanup ─────────────────────────────
  ; `schtasks /Delete /TN "LausuAutostart*"` does NOT expand the
  ; wildcard reliably across Windows versions, so we use PowerShell's
  ; Get-ScheduledTask (which DOES support wildcards in -TaskName) to
  ; enumerate matching tasks, then call schtasks /Delete for each.
  ; Best-effort: failures (no matching task, PowerShell disabled, etc.)
  ; are non-fatal, the Pop discards the exit code.
  ;
  ; Sweep widened from `LausuAutostart*` to `Lausu*` so it ALSO
  ; catches the prewarm task `LausuPrewarm` (registered by
  ; voice_typer/server/task_scheduler.py with TASK_NAME =
  ; "com.Lausu.prewarm"; the legacy pre-rename task kept the bare
  ; name `LausuPrewarm`), not just the autostart fallback tasks
  ; `LausuAutostart_<hash>`. The union with 'com.Lausu*'
  ; covers the current canonical reverse-DNS names from installs that
  ; postdate the namespace rename.
  ;
  ; NSIS string escaping: $\" is a literal double-quote. We need them
  ; around the task name so schtasks handles names with spaces correctly
  ; (unlikely for "com.Lausu.autostart_<hash>" but defensive).
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "Get-ScheduledTask -TaskName $\'Lausu*$\',$\'com.Lausu*$\' -ErrorAction SilentlyContinue | ForEach-Object { schtasks.exe /Delete /TN $\"$($_.TaskName)$\" /F }"'
  Pop $0  ; exit code, best-effort, discard

  ; Belt-and-suspenders: explicit delete of the prewarm task name in case
  ; the wildcard sweep above missed it (e.g. PowerShell Get-ScheduledTask
  ; wildcard behavior differs across Windows versions). /F = force (no
  ; prompt). Non-fatal if the task is already gone (the Pop discards the
  ; exit code). Deletes both the current canonical name and the
  ; pre-rename legacy name.
  nsExec::ExecToLog 'schtasks.exe /Delete /TN "com.Lausu.prewarm" /F'
  Pop $0  ; exit code, best-effort, discard
  nsExec::ExecToLog 'schtasks.exe /Delete /TN "LausuPrewarm" /F'
  Pop $0  ; exit code, best-effort, discard

  ; ─── CR-70: per-user data directory cleanup ────────────────────────
  ; Belt-and-suspenders: Tauri NSIS may also remove product data, but
  ; productName may be "Lausu" (with a space) while the Python
  ; backend uses "lausu" (hyphenated, lowercase) as the data dir
  ; name. We explicitly remove the latter to guarantee the data dir is
  ; purged regardless of productName / data-dir name drift.
  RMDir /r "$APPDATA\lausu"
  DetailPrint "[lausu-uninstall] Removed user data directory: $APPDATA\lausu"
!macroend
