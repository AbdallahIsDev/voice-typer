@echo off
REM Tauri Windows bundler invokes this via bundle.windows.signCommand.
REM
REM Security: the previous implementation passed the WIN_SIGN_COMMAND
REM env var through unquoted to cmd.exe, allowing shell-metacharacter injection
REM (e.g. WIN_SIGN_COMMAND="signtool sign /f cert.pfx & malicious.exe" would
REM execute malicious.exe). This rewrite uses a FIXED signtool invocation that
REM accepts only the PFX path (WIN_CSC_LINK) and password (WIN_CSC_KEY_PASSWORD)
REM as separate env vars — no arbitrary command passthrough.
REM
REM The CI workflow signs artifacts AFTER `cargo tauri build` with explicit
REM signtool steps (tauri-windows-build.yml). This wrapper is invoked by Tauri's
REM bundler during `cargo tauri build` when bundle.windows.signCommand is set.
REM It is a no-op (exit 0) when signing material is absent, so local builds
REM without signing material still succeed.
if "%WIN_CSC_LINK%"=="" exit /b 0
if "%WIN_CSC_KEY_PASSWORD%"=="" exit /b 0
signtool sign /f "%WIN_CSC_LINK%" /p "%WIN_CSC_KEY_PASSWORD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /d "Voice Typer" /du "https://voicetyper.app" "%1"
exit /b %errorlevel%
