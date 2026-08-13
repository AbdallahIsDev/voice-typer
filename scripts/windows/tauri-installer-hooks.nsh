; Tauri v2 installer hooks bundle (slim-core / runtime-pack split).
;
; Tauri's bundle.windows.nsis.installerHooks accepts a SINGLE .nsh path
; (tauri-utils deserializes it as Option<PathBuf> — a list fails the
; config parse with "invalid type: sequence", breaking the build). This
; wrapper composes the two hook files so both stay active under the
; one-path schema:
;
;   - uninstaller.nsh       -> customUnInstall (CR-69/CR-70 cleanup)
;   - installer-hooks.nsh   -> customInstall + Components-page "Include
;                              offline engine pack" consent section
;
; ${__FILEDIR__} resolves the nested includes relative to THIS file, so
; they work regardless of where the generated installer.nsi lives
; (NSIS >= 3.0). This file is Tauri-only — electron-builder's
; nsis.include still points at uninstaller.nsh directly.
!include "${__FILEDIR__}\uninstaller.nsh"
!include "${__FILEDIR__}\installer-hooks.nsh"
