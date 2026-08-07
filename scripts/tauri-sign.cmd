@echo off
REM Tauri Windows bundler invokes this via bundle.windows.signCommand.
REM
REM Security: the previous implementation passed the WIN_SIGN_COMMAND
REM env var through unquoted to cmd.exe, allowing shell-metacharacter injection
REM (e.g. WIN_SIGN_COMMAND="signtool sign /f cert.pfx & malicious.exe" would
REM execute malicious.exe). This rewrite is a FIXED adapter: it accepts only
REM the binary path as %1 and delegates to the shared PowerShell helper
REM (scripts\windows\sign-authenticode.ps1), which owns the single validated
REM signtool invocation, branding lookup, timestamp retry policy, and
REM signature verification. No arbitrary command passthrough.
REM
REM The CI workflow signs artifacts AFTER `cargo tauri build` with explicit
REM signtool steps (tauri-windows-build.yml). This wrapper is invoked by Tauri's
REM bundler during `cargo tauri build` when bundle.windows.signCommand is set.
REM It is a no-op (exit 0) when signing material is absent, so local builds
REM without signing material still succeed.
if "%WIN_CSC_LINK%" == "" exit /b 0
if "%WIN_CSC_KEY_PASSWORD%" == "" exit /b 0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\sign-authenticode.ps1" -BinaryPath "%~1"
exit /b %errorlevel%
