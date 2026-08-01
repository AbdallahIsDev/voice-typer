@echo off
REM Tauri Windows bundler invokes this via bundle.windows.signCommand.
REM The CI workflow signs artifacts AFTER `cargo tauri build` with explicit
REM signtool steps (tauri-windows-build.yml), so this wrapper only signs when
REM WIN_SIGN_COMMAND is explicitly set; otherwise it is a no-op so local
REM builds without signing material still succeed.
if "%WIN_SIGN_COMMAND%"=="" exit /b 0
%WIN_SIGN_COMMAND% "%1"
exit /b %errorlevel%
