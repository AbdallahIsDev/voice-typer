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

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$SourceFile = Join-Path $ProjectRoot "voice_typer\server\native\windows-key-listener.c"
$OutputDir  = Join-Path $ProjectRoot "src-tauri\resources\native"
$OutputExe  = Join-Path $OutputDir "windows-key-listener.exe"

# Ensure output directory exists
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "[compile_native] Source: $SourceFile"
Write-Host "[compile_native] Output: $OutputExe"

# Check if the source exists
if (-not (Test-Path $SourceFile)) {
    Write-Error "Source file not found: $SourceFile"
    exit 1
}

# Try to find MSVC compiler (cl.exe)
$clPath = $null

# 1. Try vswhere (VS 2017+)
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $vsPath = & $vswhere -latest -property installationPath
    if ($vsPath) {
        # Common MSVC tool locations under the VS install
        $msvcSearchPaths = @(
            "$vsPath\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe",
            "$vsPath\VC\Tools\MSVC\*\bin\Hostx86\x64\cl.exe",
            "$vsPath\VC\bin\cl.exe"
        )
        foreach ($pattern in $msvcSearchPaths) {
            $found = Get-ChildItem $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) {
                $clPath = $found.FullName
                break
            }
        }
    }
}

# 2. Try VS Build Tools environment variables
if (-not $clPath) {
    # Check for VS 2022 (latest)
    $VsDir = "${env:ProgramFiles}\Microsoft Visual Studio\2022"
    if (-not (Test-Path $VsDir)) {
        $VsDir = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022"
    }
    if (-not (Test-Path $VsDir)) {
        $VsDir = "${env:ProgramFiles}\Microsoft Visual Studio\2019"
    }
    if (-not (Test-Path $VsDir)) {
        $VsDir = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2019"
    }

    if (Test-Path $VsDir) {
        # Try the most common MSVC paths
        foreach ($edition in @("Enterprise", "Professional", "Community", "BuildTools")) {
            $msvcPatterns = @(
                "$VsDir\$edition\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe",
                "$VsDir\$edition\VC\Tools\MSVC\*\bin\Hostx86\x64\cl.exe",
                "$VsDir\$edition\VC\bin\cl.exe"
            )
            foreach ($pattern in $msvcPatterns) {
                $found = Get-ChildItem $pattern -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($found) {
                    $clPath = $found.FullName
                    break
                }
            }
            if ($clPath) { break }
        }
    }
}

# 3. Fallback: check PATH for cl.exe
if (-not $clPath) {
    $clPath = (Get-Command "cl.exe" -ErrorAction SilentlyContinue).Source
}

if (-not $clPath) {
    Write-Warning "[compile_native] MSVC cl.exe not found via standard paths."
    Write-Warning "[compile_native] Attempting to use Visual Studio Developer Command Prompt..."

    # Try to find and run vcvarsall.bat first, then call cl.exe
    $vcvarsPaths = @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
    )

    $vcvarsFound = $null
    foreach ($v in $vcvarsPaths) {
        if (Test-Path $v) {
            $vcvarsFound = $v
            break
        }
    }

    if ($vcvarsFound) {
        Write-Host "[compile_native] Found vcvars64.bat at: $vcvarsFound"
        # Use cmd to run vcvarsall.bat then cl.exe
        $cmd = "`"$vcvarsFound`" > nul 2>&1 && cl.exe /O2 `"$SourceFile`" /link user32.lib /out:`"$OutputExe`""
        cmd /c $cmd 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "MSVC compilation failed with exit code $LASTEXITCODE"
            exit 1
        }
    } else {
        Write-Error "MSVC (cl.exe) not found. Install Visual Studio Build Tools with the 'Desktop development with C++' workload."
        Write-Error "Download: https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022"
        exit 1
    }
} else {
    Write-Host "[compile_native] Using MSVC: $clPath"
    # Build the binary directly
    & $clPath "/O2" $SourceFile "/link" "user32.lib" "/out:$OutputExe"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "MSVC compilation failed with exit code $LASTEXITCODE"
        exit 1
    }
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
