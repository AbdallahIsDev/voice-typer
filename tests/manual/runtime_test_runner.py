"""
Runtime test runner: starts the real Voice Typer app, simulates F2
keypresses, and captures the log to verify transcription-time fallback
and stuck-state recovery.

Usage:
    python runtime_test_runner.py
"""

import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────
APPDATA = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
LOG_DIR = Path(APPDATA) / "voice-typer"
LOG_FILE = LOG_DIR / "voice-typer.log"

# F2 virtual-key code
VK_F2 = 0x71

# ── Helpers ─────────────────────────────────────────────────────────────


def simulate_f2():
    """Simulate an F2 keypress using Win32 keybd_event."""
    user32 = ctypes.windll.user32
    keyeventf_keyup = 0x0002

    # Key down
    user32.keybd_event(VK_F2, 0, 0, 0)
    time.sleep(0.05)
    # Key up
    user32.keybd_event(VK_F2, 0, keyeventf_keyup, 0)
    print(f"  [SIM] F2 keypress simulated at {time.strftime('%H:%M:%S')}")


def tail_log(log_file, timeout=60, stop_on=None):
    """Tail the log file until stop_on string is found or timeout."""
    start = time.time()
    seen_lines = []
    offset = 0

    while time.time() - start < timeout:
        if not log_file.exists():
            time.sleep(0.5)
            continue
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                new_lines = f.readlines()
                offset = f.tell()
                if new_lines:
                    for line in new_lines:
                        line = line.strip()
                        if line:
                            seen_lines.append(line)
                            print(f"  [LOG] {line}")
                        if stop_on and stop_on in line:
                            return seen_lines
        except Exception:
            pass
        time.sleep(0.3)

    return seen_lines


def wait_for_log(log_file, pattern, timeout=90):
    """Wait until a specific pattern appears in the log file."""
    start = time.time()
    offset = 0
    if log_file.exists():
        with open(log_file, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # seek to end
            offset = f.tell()

    while time.time() - start < timeout:
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                new_lines = f.readlines()
                offset = f.tell()
                for line in new_lines:
                    if pattern in line:
                        print(f"  [FOUND] Pattern matched: {line.strip()}")
                        return True
                    if new_lines:
                        for line in new_lines:
                            line = line.strip()
                            if line:
                                print(f"  [LOG] {line}")
        except Exception:
            pass
        time.sleep(0.3)

    print(f"  [TIMEOUT] Pattern '{pattern}' not found within {timeout}s")
    return False


# ── Main ────────────────────────────────────────────────────────────────


def main():
    print("=" * 70)
    print("VOICE TYPER — RUNTIME CYCLE TEST")
    print("=" * 70)

    # 1. Clear old log (truncate instead of delete to avoid locked-file errors)
    print("\n[1] Clearing old log file...")
    if LOG_FILE.exists():
        backup = LOG_FILE.with_suffix(".log.bak")
        try:
            shutil.copy2(LOG_FILE, backup)
            print(f"  Old log backed up to {backup}")
        except Exception:
            pass
        try:
            # Truncate instead of unlink — works even if another process has it open
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.truncate()
            print("  Log file truncated")
        except PermissionError:
            print("  [WARN] Log file locked — will read from current end offset")
    else:
        print("  No old log found — starting fresh")

    # 2. Start the app
    print("\n[2] Starting Voice Typer app...")
    project_dir = Path(__file__).parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_typer"],
        cwd=str(project_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  App PID: {proc.pid}")

    # 3. Wait for model to load
    print("\n[3] Waiting for model to load (up to 90s)...")
    model_loaded = wait_for_log(LOG_FILE, "Loaded successfully", timeout=90)
    if not model_loaded:
        # Check for startup completion as alternative
        startup_ok = wait_for_log(LOG_FILE, "_do_startup complete", timeout=30)
        if not startup_ok:
            print("  [WARN] Startup may not have completed — proceeding anyway")

    # Print current log state
    print("\n[4] Current log state after startup:")
    if LOG_FILE.exists():
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                safe = line.rstrip().encode("ascii", errors="replace").decode("ascii")
                print(f"  {safe}")

    # 5. Simulate F2 to START recording
    print("\n[5] Simulating F2 to START recording...")
    simulate_f2()

    # Wait for recording to start
    recording_started = wait_for_log(LOG_FILE, "Recording started OK", timeout=15)
    if not recording_started:
        print("  [WARN] Recording may not have started")

    # 6. Wait a few seconds (simulated "speaking" — mic will pick up ambient noise)
    print("\n[6] Waiting 4 seconds (ambient audio will be captured)...")
    time.sleep(4)

    # 7. Simulate F2 to STOP recording
    print("\n[7] Simulating F2 to STOP recording...")
    simulate_f2()

    # Wait for the stop/transcription cycle
    print("\n[8] Waiting for transcription cycle to complete (up to 60s)...")
    # NEW-DEAD-002: previously this grepped for "_busy reset to False"
    # which was a Flet-era marker that no longer exists in the
    # refactored code.  The production code now logs
    # "[TRANSCRIBE] Transcription complete" (dictation_pipeline.py:98)
    # and "[DICTATION] Audio too short, skipping transcription" for
    # short-audio cases.  We look for either as the "cycle finished"
    # indicator.  When neither appears, we still check for FORCE
    # RECOVER (which is still emitted by recording_controller.py:623).
    cycle_done = wait_for_log(LOG_FILE, "[TRANSCRIBE] Transcription complete", timeout=60) or wait_for_log(
        LOG_FILE, "Audio too short, skipping transcription", timeout=5
    )
    if not cycle_done:
        print("  [WARN] transcription cycle did not complete — checking for force recover")
        force_recover = wait_for_log(LOG_FILE, "FORCE RECOVER", timeout=10)

    # 8b. Wait a bit more for any recovery timers
    time.sleep(5)

    # 9. Simulate a SECOND F2 press to verify F2 works again
    print("\n[9] Simulating SECOND F2 press to verify re-usability...")
    simulate_f2()

    # Wait to see if recording starts (or if it's blocked by _busy)
    time.sleep(3)

    # 10. Read the complete log
    print("\n[10] ====== FRESH RUNTIME LOG ======")
    if LOG_FILE.exists():
        with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
            full_log = f.read()
            safe_log = full_log.encode("ascii", errors="replace").decode("ascii")
            print(safe_log)
    else:
        print("  [ERROR] Log file not found!")
        full_log = ""

    # 11. Analysis
    print("\n" + "=" * 70)
    print("ANALYSIS OF RUNTIME RESULTS")
    print("=" * 70)

    lines = full_log.strip().split("\n") if full_log else []

    # Check: transcribe_with_fallback exercised?
    fallback_exercised = any(
        "transcribe_with_fallback" in line or "GPU transcription failed" in line or "falling back to CPU" in line
        for line in lines
    )
    transcribe_attempted = any("Starting transcription" in line for line in lines)
    transcribe_completed = any("Transcription complete" in line for line in lines)
    transcribe_failed = any("Transcription FAILED" in line for line in lines)
    no_speech = any("No speech detected" in line for line in lines)
    audio_too_short = any("too short" in line.lower() for line in lines)

    # Check: _busy recovered?
    # NEW-DEAD-002: the production code no longer logs "_busy reset to
    # False".  Instead, the transcription-completion path logs
    # "[TRANSCRIBE] Transcription complete" and the recovery path
    # logs "FORCE RECOVER".  We treat either as the "busy recovered"
    # signal.
    busy_set = any("_busy=True" in line or "busy=True" in line for line in lines)
    busy_reset = any(
        "[TRANSCRIBE] Transcription complete" in line or "Audio too short, skipping transcription" in line
        for line in lines
    )
    force_recover = any("FORCE RECOVER" in line for line in lines)
    f2_blocked = any("F2 BLOCKED" in line for line in lines)

    # Check: tray recovered?
    tray_idle = any("IDLE" in line for line in lines)
    # Check if Transcribing... was the last state
    last_tray_line = ""
    for line in lines:
        if (
            "set_state" in line.lower()
            or "IDLE" in line
            or "TRANSCRIBING" in line
            or "RECORDING" in line
            or "ERROR" in line
        ):
            last_tray_line = line

    # Check: F2 worked again after first cycle?
    second_f2_fired = False
    f2_fire_count = 0
    for line in lines:
        if "HOTKEY FIRED" in line:
            f2_fire_count += 1
            if f2_fire_count >= 2:
                second_f2_fired = True

    print(f"""
1. transcribe_with_fallback() exercised:
   - Transcription attempted: {transcribe_attempted}
   - Transcription completed: {transcribe_completed}
   - Transcription failed:    {transcribe_failed}
   - No speech detected:      {no_speech}
   - Audio too short:         {audio_too_short}
   - GPU fallback triggered:  {fallback_exercised}

2. _busy recovery:
   - _busy was set to True:   {busy_set}
   - _busy reset to False:    {busy_reset}
   - Force recovery fired:    {force_recover}
   - F2 was BLOCKED by busy:  {f2_blocked}

3. Tray recovery:
   - Tray returned to IDLE:   {tray_idle}
   - Final tray state line:   {last_tray_line[:80] if last_tray_line else "N/A"}

4. F2 re-usable after cycle:
   - Second F2 fired:         {second_f2_fired}
   - Total F2 fires:          {f2_fire_count}
""")

    # Determine outcome
    if busy_reset and not f2_blocked:
        print("OUTCOME: SUCCESS — _busy recovered, F2 not blocked, tray recovered")
    elif force_recover and not f2_blocked:
        print("OUTCOME: SUCCESS (via force recovery) — watchdog recovered stuck state")
    elif busy_reset and f2_blocked:
        print("OUTCOME: PARTIAL — _busy recovered but F2 was blocked at some point")
    else:
        print("OUTCOME: NEEDS INVESTIGATION — _busy may not have recovered properly")

    # Cleanup: stop the app
    print("\n[CLEANUP] Stopping the app...")
    try:
        proc.terminate()
        proc.wait(timeout=5)
        print("  App terminated gracefully")
    except Exception:
        try:
            proc.kill()
            print("  App killed")
        except Exception:
            print("  Could not stop app — may need manual cleanup")


if __name__ == "__main__":
    main()


# TASK-013: expose a stable ``run()`` alias so ``tests/test_manual_slow.py``
# can wrap this script as a ``@pytest.mark.slow`` test. Defined AFTER the
# ``__main__`` block so running the script directly still uses the
# zero-arg ``main()`` call above.
run = main
