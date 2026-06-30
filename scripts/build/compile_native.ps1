# =============================================================================
# Voice Typer — Native key-listener binary build script (Windows PowerShell)
#
# Compiles the native key-listener binary for Windows:
#   voice_typer/server/native/windows-key-listener.exe (C)
#
# This is the Windows equivalent of scripts/build/compile_native.sh.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build\compile_native.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build\compile_native.ps1 -Check
# =============================================================================
[CmdletBinding()]
param(
    [switch]$Check
)

$ErrorActionPreference = "Stop"

# Resolve project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$NativeDir = Join-Path $ProjectRoot "voice_typer\server\native"

Write-Host "[compile_native] Project root: $ProjectRoot"
Write-Host "[compile_native] Native dir:   $NativeDir"

# ─── Locate toolchain ───────────────────────────────────────────────────────
$clPath = (Get-Command cl.exe -ErrorAction SilentlyContinue).Source
$gccPath = (Get-Command gcc.exe -ErrorAction SilentlyContinue).Source

if ($Check) {
    if ($clPath) {
        Write-Host "[compile_native] OK: cl.exe found at $clPath"
        exit 0
    } elseif ($gccPath -and (& $gccPath --version 2>&1 | Select-String -Quiet "MINGW")) {
        Write-Host "[compile_native] OK: MinGW gcc found at $gccPath"
        exit 0
    } else {
        Write-Host "[compile_native] MISSING: neither cl.exe (MSVC) nor MinGW gcc found."
        Write-Host "  Install Visual Studio Build Tools or MinGW-w64."
        exit 1
    }
}

# ─── Build ─────────────────────────────────────────────────────────────────
$Src = Join-Path $NativeDir "windows-key-listener.c"
$Out = Join-Path $NativeDir "windows-key-listener.exe"

if (-not (Test-Path $Src)) {
    Write-Error "[compile_native] ERROR: source not found: $Src"
    exit 1
}

if ($clPath) {
    Write-Host "[compile_native] Compiling with MSVC: cl.exe /O2 $Src"
    & $clPath /nologo /O2 /D_CRT_SECURE_NO_WARNINGS /D_WIN32_WINNT=0x0600 `
        $Src /link /NOLOGO user32.lib kernel32.lib `
        /OUT:$Out
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[compile_native] cl.exe failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} elseif ($gccPath -and (& $gccPath --version 2>&1 | Select-String -Quiet "MINGW")) {
    Write-Host "[compile_native] Compiling with MinGW: gcc -O2 $Src -o $Out -luser32"
    & $gccPath -O2 -std=c99 -D_WIN32_WINNT=0x0600 `
        $Src -o $Out -luser32
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[compile_native] gcc failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} else {
    Write-Error "[compile_native] ERROR: neither cl.exe (MSVC) nor MinGW gcc found."
    Write-Host "  Install Visual Studio Build Tools or MinGW-w64."
    exit 1
}

Write-Host "[compile_native] OK: $Out"
Write-Host "[compile_native] Done."
