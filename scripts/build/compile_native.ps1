# compile_native.ps1 — Build the native Windows key-listener binary.
#
# This script compiles voice_typer/server/native/windows-key-listener.c
# into a standalone windows-key-listener.exe using MSVC (cl.exe), which
# is available on GitHub Actions windows-2022 runners and on any Windows
# dev machine with Visual Studio Build Tools installed.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/build/compile_native.ps1
#
# The output binary is written to:
#   src-tauri/resources/native/windows-key-listener.exe
#
# Build command (from the .c source header):
#   cl.exe /O2 windows-key-listener.c /link user32.lib /out:windows-key-listener.exe
#
# SPDX-License-Identifier: MIT

param([switch]$Check)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$SourceFile = Join-Path $ProjectRoot "voice_typer\server\native\windows-key-listener.c"
$OutputDir  = Join-Path $ProjectRoot "src-tauri\resources\native"
$OutputExe  = Join-Path $OutputDir "windows-key-listener.exe"

# ─── -Check mode: probe the toolchain and exit (mirrors compile_native.sh
# --check for win32 — WR-18 FINDING A-1). The sibling bash script
# build_native_listener_windows.sh invokes `compile_native.ps1 -Check`;
# previously this script had no param() block so PowerShell rejected the
# -Check argument with "A parameter cannot be found that matches parameter
# name 'Check'." The probe below verifies (a) the C source file exists and
# (b) an MSVC toolchain is reachable (cl.exe on PATH, or vswhere.exe
# present, or vcvars64.bat discoverable) — exits 0 if OK, 1 if missing.
if ($Check) {
    Write-Host "[compile_native] -Check: verifying toolchain"

    # (a) Source file must exist.
    if (-not (Test-Path $SourceFile)) {
        Write-Error "[compile_native] MISSING: source file not found: $SourceFile"
        exit 1
    }
    Write-Host "[compile_native] OK: source file found: $SourceFile"

    # (b) cl.exe on PATH?
    $clOnPath = $null
    $clOnPath = Get-Command "cl.exe" -ErrorAction SilentlyContinue
    if ($clOnPath) {
        Write-Host "[compile_native] OK: cl.exe found on PATH at $($clOnPath.Source)"
        exit 0
    }

    # (c) vswhere.exe present? (Implies a VS install — cl.exe reachable via vcvars.)
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $vsPath = & $vswhere -latest -property installationPath
        if ($vsPath) {
            Write-Host "[compile_native] OK: Visual Studio found at $vsPath (cl.exe available via vcvars)"
            exit 0
        }
    }

    # (d) vcvars64.bat discoverable? (VS Build Tools may be installed without vswhere on PATH.)
    $vcvarsCandidates = @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
    )
    foreach ($v in $vcvarsCandidates) {
        if (Test-Path $v) {
            Write-Host "[compile_native] OK: vcvars64.bat found at $v (cl.exe available via vcvars)"
            exit 0
        }
    }

    Write-Error "[compile_native] MISSING: neither cl.exe (MSVC) nor vswhere.exe nor vcvars64.bat found."
    Write-Error "  Install Visual Studio Build Tools with the 'Desktop development with C++' workload."
    Write-Error "  Download: https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022"
    exit 1
}

# Ensure output directory exists
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "[compile_native] Source: $SourceFile"
Write-Host "[compile_native] Output: $OutputExe"

# Check if the source exists
if (-not (Test-Path $SourceFile)) {
    Write-Error "Source file not found: $SourceFile"
    exit 1
}

# ─── Locate vcvars64.bat ────────────────────────────────────────────────────
# CRITICAL: cl.exe cannot find windows.h (or any standard SDK header) unless
# the INCLUDE / LIB / LIBPATH environment variables are populated first.
# Sourcing vcvars64.bat (the VS "Developer Command Prompt" bootstrap) is the
# ONLY correct way to set those — invoking cl.exe directly (as this script
# did previously) fails with `fatal error C1034: windows.h: no include path
# set` because cl.exe only knows the compiler binary's own location, not the
# Windows SDK / MSVC headers.
#
# Discovery order:
#   1. vswhere.exe — the canonical VS 2017+ discovery tool.
#   2. Hard-coded VS 2022 / 2019 install paths (Build Tools + per-edition).
#   3. vcvars64.bat already on PATH (rare; cl.exe would also be there).
$vcvarsFound = $null

# 1. vswhere (VS 2017+) — canonical discovery.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -property installationPath
    if ($vsPath) {
        $candidate = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
        if (Test-Path $candidate) { $vcvarsFound = $candidate }
    }
}

# 2. Hard-coded VS 2022 / 2019 paths (Build Tools + per-edition).
if (-not $vcvarsFound) {
    $vcvarsCandidates = @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2019\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2019\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
    )
    foreach ($v in $vcvarsCandidates) {
        if (Test-Path $v) { $vcvarsFound = $v; break }
    }
}

if (-not $vcvarsFound) {
    Write-Error "MSVC toolchain not found. Install Visual Studio Build Tools with the 'Desktop development with C++' workload."
    Write-Error "  Download: https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022"
    exit 1
}

Write-Host "[compile_native] Using vcvars64.bat: $vcvarsFound"

# ─── Compile via a temp batch wrapper that sources vcvars64.bat ─────────────
# vcvars64.bat populates INCLUDE / LIB / LIBPATH and prepends the MSVC bin
# dir to PATH, then we chain into cl.exe. We MUST run this through cmd.exe
# (not PowerShell's `&` invocation of cl.exe directly) because the env-var
# setup happens in the cmd subprocess and only applies inside that process.
#
# Why write a temp .bat instead of `cmd /c "$vcvars && cl.exe ..."`:
#   When PowerShell passes a single string containing multiple quote chars
#   to `cmd /c`, cmd's quote-stripping rule (see `cmd /?` → /C) removes the
#   FIRST and LAST quote characters from the command line, which corrupts
#   paths containing spaces (e.g. "C:\Program Files\Microsoft Visual
#   Studio\..."). Writing a temp batch file and invoking `cmd /c <file>`
#   avoids the issue entirely — no string quote-stripping possible.
$batchContent = @"
@echo off
call `"$vcvarsFound`" >nul
if errorlevel 1 exit /b %errorlevel%
cl.exe /O2 /nologo `"$SourceFile`" /link user32.lib /nologo /out:`"$OutputExe`"
exit /b %errorlevel%
"@
$batchFile = Join-Path $env:TEMP "voice-typer_compile_native_$(Get-Random).bat"
Set-Content -Path $batchFile -Value $batchContent -Encoding ASCII
try {
    & cmd /c $batchFile
    $exitCode = $LASTEXITCODE
} finally {
    Remove-Item -Path $batchFile -Force -ErrorAction SilentlyContinue
}
if ($null -eq $exitCode) { $exitCode = 1 }
if ($exitCode -ne 0) {
    Write-Error "MSVC compilation failed with exit code $exitCode"
    exit 1
}

# Verify output
if (-not (Test-Path $OutputExe)) {
    Write-Error "Output binary not found: $OutputExe"
    exit 1
}

$size = (Get-Item $OutputExe).Length / 1KB
Write-Host "[compile_native] SUCCESS: $OutputExe ($([math]::Round($size,1)) KB)"

# Update the binary manifest SHA-256 (optional, for CI)
$sha = (Get-FileHash -Algorithm SHA256 -Path $OutputExe).Hash.ToLower()
Write-Host "[compile_native] SHA-256: $sha"
