# CI-06: Windows smoke test helper for the native key-listener binary.
#
# Starts the binary, waits for READY (proving WH_KEYBOARD_LL hook installed),
# then sends a synthetic CapsLock keystroke via SendInput and verifies the
# hook callback fired (KEY_DOWN:CapsLock in stdout).
#
# Usage: .\smoke_test_windows.ps1 <path-to-binary> <hotkey-spec>
#   e.g.  .\smoke_test_windows.ps1 windows-key-listener.exe '<caps_lock>'

param(
    [Parameter(Mandatory=$true)]
    [string]$BinaryPath,

    [Parameter(Mandatory=$true)]
    [string]$HotkeySpec
)

$ErrorActionPreference = "Stop"

# --- Start the binary with redirected stdout ---
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $BinaryPath
$psi.Arguments = $HotkeySpec
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)

# Async stdout reader (non-blocking) so we can check output while
# the binary is still running.
$outputBuilder = New-Object System.Text.StringBuilder
Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived `
    -Action { $outputBuilder.AppendLine($EventArgs.Data) } > $null
$proc.BeginOutputReadLine()

# --- Wait up to 3s for READY ---
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 100
    if ($outputBuilder.ToString() -match "READY") {
        $ready = $true
        break
    }
}

if (-not $ready) {
    Write-Error "Smoke test FAILED: binary did not emit READY within 3s"
    $output = $outputBuilder.ToString()
    Write-Host "Output so far: $output"
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    exit 1
}
Write-Host "Binary emitted READY (WH_KEYBOARD_LL hook installed)"

# --- Send a synthetic CapsLock keystroke via SendInput ---
# We use a C# inline type to call the Win32 SendInput API.
# VK_CAPITAL = 0x14
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class SendInputHelper {
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)]
    public struct INPUT {
        [FieldOffset(0)] public uint type;
        [FieldOffset(4)] public KEYBDINPUT ki;
    }
    [DllImport("user32.dll", SetLastError=true)]
    public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    public static void PressKey(ushort vk) {
        INPUT[] inputs = new INPUT[1];
        inputs[0].type = 1; // KEYBOARD
        inputs[0].ki.wVk = vk;
        inputs[0].ki.dwFlags = 0; // key down
        SendInput(1, inputs, Marshal.SizeOf(typeof(INPUT)));
        inputs[0].ki.dwFlags = 2; // KEYEVENTF_KEYUP
        SendInput(1, inputs, Marshal.SizeOf(typeof(INPUT)));
    }
}
"@

[SendInputHelper]::PressKey(0x14)
Start-Sleep -Milliseconds 500

$output = $outputBuilder.ToString()
Write-Host "Full output: $output"

$hookFired = $output -match "KEY_DOWN:CapsLock"
if ($hookFired) {
    Write-Host "Smoke test PASSED (hook callback fired: KEY_DOWN:CapsLock received)"
} else {
    # CI-06: require the hook to fire. CI desktop sessions normally DO
    # forward synthetic input via SendInput, so a missing KEY_DOWN means
    # the WH_KEYBOARD_LL callback is broken — a real regression we must
    # not let pass. If a specific runner genuinely cannot forward synthetic
    # input, set VOICE_TYPER_ALLOW_SKIP_HOOK_FIRE=1 to downgrade to a
    # warning (READY alone still proves the hook installed).
    $allowSkip = $env:VOICE_TYPER_ALLOW_SKIP_HOOK_FIRE -eq "1"
    if ($allowSkip) {
        Write-Host "Smoke test PASSED with caveat (READY received but no KEY_DOWN:CapsLock; VOICE_TYPER_ALLOW_SKIP_HOOK_FIRE=1)"
    } else {
        Write-Error "Smoke test FAILED: READY received but hook did not fire (no KEY_DOWN:CapsLock). WH_KEYBOARD_LL callback may be broken."
        exit 1
    }
}

if (-not $proc.HasExited) {
    Stop-Process -Id $proc.Id -Force
}
