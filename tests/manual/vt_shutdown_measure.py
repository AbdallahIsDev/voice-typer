"""Measure end-to-end shutdown latency: spawn backend, auth, send quit_app, time exit."""
import json
import os
import secrets
import socket
import subprocess
import sys
import time


def main() -> None:
    port = 19911
    token = secrets.token_hex(16)
    env = dict(os.environ)
    env["VOICE_TYPER_IPC_TOKEN"] = token
    env["PYTHONUNBUFFERED"] = "1"
    env["VOICE_TYPER_CONFIG_DIR"] = os.path.join(os.environ.get("TEMP", "/tmp"), "vt_shutdown_measure")

    # Context manager (SIM115): logf stays open for the subprocess's whole
    # lifetime (it is the child's stdout/stderr), then closes on exit.
    with open("shutdown_trace2.log", "w", encoding="utf-8") as logf:
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            [sys.executable, "-m", "voice_typer.server.ipc_server", "--port", str(port)],
            cwd=".",
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = t0 + 90
            sock = None
            while time.perf_counter() < deadline:
                if proc.poll() is not None:
                    print(f"backend exited early rc={proc.returncode}")
                    return
                s = socket.socket()
                s.settimeout(0.3)
                try:
                    s.connect(("127.0.0.1", port))
                    sock = s
                    break
                except OSError:
                    s.close()
                    time.sleep(0.1)
            if sock is None:
                print("never connected")
                return
            sock.settimeout(30)
            f = sock.makefile("rw", encoding="utf-8")
            f.write(json.dumps({"type": "auth", "token": token, "protocol_version": 1}) + "\n")
            f.flush()
            # drain until ready event (backend pushes {'type':'ready'})
            ready_at = None
            while True:
                line = f.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("type") == "ready":
                    ready_at = time.perf_counter()
                    break
            if ready_at is None:
                print("never got ready")
                return
            print(f"boot to ready: {ready_at - t0:.2f}s")

            t_quit = time.perf_counter()
            f.write(json.dumps({"id": 1, "type": "quit_app"}) + "\n")
            f.flush()
            # wait for process exit
            rc = proc.wait(timeout=30)
            t_exit = time.perf_counter()
            print(f"quit_app -> process exit: {t_exit - t_quit:.2f}s (rc={rc})")
            print(f"total from spawn: {t_exit - t0:.2f}s")
        finally:
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    main()
