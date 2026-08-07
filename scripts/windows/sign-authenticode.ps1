# sign-authenticode.ps1 - centralized, validated Windows Authenticode signing.
#
# Security: this helper accepts ONLY the binary path as a positional argument
# and reads signing material from the WIN_CSC_LINK / WIN_CSC_KEY_PASSWORD /
# WIN_CSC_BASE64 environment variables. It never expands an arbitrary signing
# command (no dynamic signtool arguments derived from input). Tauri's
# bundler invokes it through scripts\tauri-sign.cmd
# (bundle.windows.signCommand), and CI workflows call it for the sidecar,
# prewarm, native listener, standalone exe, NSIS and MSI artifacts so every
# PE ships the same validated arguments, branding lookup, timestamp retry
# policy, and signature verification.
#
# No-op (exit 0) when signing material is absent so local dev builds that
# never set WIN_CSC_* keep working.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$BinaryPath
)

$ErrorActionPreference = "Stop"

# C-BRAND-1: the Authenticode description MUST be derived from the branding
# source of truth (voice_typer/server/branding.py) - never inline the app
# name here, or a rename would silently diverge from the signed binaries.
$brandingFile = Join-Path $PSScriptRoot "..\..\voice_typer\server\branding.py"
if (-not (Test-Path $brandingFile)) {
    throw "branding source of truth not found at $brandingFile"
}
$brandMatch = Select-String -Path $brandingFile -Pattern '^APP_NAME\s*=\s*"([^"]+)"'
if (-not $brandMatch) {
    throw "APP_NAME not found in voice_typer/server/branding.py"
}
$sigDescription = $brandMatch.Matches[0].Groups[1].Value

# No signing material configured -> no-op (local builds stay green).
if ([string]::IsNullOrWhiteSpace($env:WIN_CSC_LINK) -and [string]::IsNullOrWhiteSpace($env:WIN_CSC_BASE64)) { exit 0 }
if ([string]::IsNullOrWhiteSpace($env:WIN_CSC_KEY_PASSWORD)) { exit 0 }

$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $signtool) { throw "signtool.exe not found (install the Windows SDK)" }

$tempPfx = $null
$pfxPath = $env:WIN_CSC_LINK
try {
    # WIN_CSC_BASE64 support: some secret stores cannot carry a multi-line
    # PFX, so CI may pass the cert base64-encoded. Materialize it to a temp
    # file, sign, and remove it in the finally block below.
    if (-not [string]::IsNullOrWhiteSpace($env:WIN_CSC_BASE64)) {
        $tempPfx = Join-Path $env:TEMP ("codesign-" + [guid]::NewGuid().ToString("N") + ".pfx")
        [System.IO.File]::WriteAllBytes($tempPfx, [System.Convert]::FromBase64String($env:WIN_CSC_BASE64))
        $pfxPath = $tempPfx
    }
    if ([string]::IsNullOrWhiteSpace($pfxPath)) { exit 0 }

    $timestampServers = @(
        "http://timestamp.digicert.com",
        "http://timestamp.sectigo.com",
        "http://timestamp.globalsign.com/scripts/timestamp.dll",
        "http://ts.ssl.com"
    )
    $maxAttempts = 3
    foreach ($attempt in 1..$maxAttempts) {
        foreach ($ts in $timestampServers) {
            & $signtool sign /f $pfxPath /p $env:WIN_CSC_KEY_PASSWORD `
                /fd SHA256 /tr $ts /td SHA256 /d "$sigDescription" /du "https://voicetyper.app" $BinaryPath
            if ($LASTEXITCODE -eq 0) {
                # Verify the signature landed before declaring success.
                & $signtool verify /pa /v $BinaryPath
                exit 0
            }
            Start-Sleep -Seconds 30
        }
    }
    throw "signtool sign failed after $maxAttempts attempts across all timestamp servers"
}
finally {
    if ($tempPfx) { Remove-Item -Force $tempPfx -ErrorAction SilentlyContinue }
}
