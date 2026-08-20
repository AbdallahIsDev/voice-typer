"""Shared Electron-build helpers for the two launcher modules.

Single source of truth for the **locate → ``electron .`` → ``npm run dev``
fallback** strategy that was previously copy-pasted into both
:mod:`voice_typer.server.electron_launcher` and
:mod:`voice_typer.server.autostart_launcher`.

Why this module exists
----------------------
The two launchers each defined their own copies of ``_electron_binary``,
``_main_entry_built``, ``_npm_command``, ``_spawn_flags`` and
``_electron_log_files``.  Bug fixes had to be
applied to both copies, and they had already drifted in intent:

* ``autostart_launcher._npm_command`` carried the
  fix (avoid ``shell=True`` on POSIX when ``shutil.which("npm")`` misses
  by returning the list form ``["npm", "run", script]``), while
  ``electron_launcher._npm_command`` still returned ``None`` (forcing
  ``shell=True``).
* ``autostart_launcher._spawn_flags`` took a ``hidden`` kwarg so the
  autostart-at-login path could suppress the Windows console flash while
  the desktop-shortcut path left normal creation flags.  The electron
  launcher had no such knob because it always wants the hidden behaviour.

Unifying on this module means future fixes land in one place.  All
functions are stateless; platform detection goes through
:mod:`voice_typer.server.platform_utils` so Windows / macOS / Linux all
behave correctly.

Strategy summary
----------------
1. **Locate** the dev-mode Electron binary
   (``node_modules/electron/dist/electron[.exe]``).
2. **Verify the pre-built bundles** (``out/main/index.js``) exist — the
   app is NEVER built from source at launch time.  A packaged install
   ships pre-built bundles; when they are missing the launcher fails
   fast so the caller can fall back to the dev path.
3. **Launch ``electron .``** with ``VT_PYTHON_PORT`` / ``VT_IPC_TOKEN``
   env vars (set by the caller) so Electron's main process connects to
   the Python backend instead of spawning its own.
4. **Last-resort fallback**: if the binary is missing or the pre-built
   bundles are absent, fall back to ``npm run dev`` (Vite dev server +
   Electron).

The orchestration of these steps (when to fall back, what env vars to
set, what to do with the child PID) lives in the launcher modules — this
file only provides the primitives.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

# Directory layout (mirrors both launcher modules):
#   <root>/
#     voice_typer/
#       server/
#         _electron_build.py    <- this file
#       client/                  <- Electron app
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLIENT_DIR = BASE_DIR / "voice_typer" / "client"


def _electron_binary() -> str | None:
    """Return the path to the dev-mode Electron binary, or ``None`` if absent.

        In dev mode Electron ships under
        ``node_modules/electron/dist/electron.exe`` (Windows) /
        ``.../electron`` (POSIX).  Returns ``None`` when not found, in which
        case the caller falls back to ``npm run dev`` (which itself starts
        Electron via the npm script).

    optional integrity verification. When the environment
        variable ``VOICE_TYPER_ELECTRON_SHA256`` is set to a 64-char hex
        SHA-256, the binary is hashed on disk and compared against the
        expected value. On mismatch, the function logs an ERROR and
        returns ``None`` (forcing the caller to skip this binary and fall
        back to ``npm run dev``). When the env var is unset, behaviour is
        unchanged (no hash check; npm install integrity already covers the
        download). Operators who pin a known-good Electron build set the
        env var to detect a tampered or accidentally-upgraded binary.
    """
    if is_windows():
        candidate = CLIENT_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
    else:
        candidate = CLIENT_DIR / "node_modules" / "electron" / "dist" / "electron"
    if not candidate.exists():
        return None
    # optional SHA-256 verification. Only run when the
    # operator has provided an expected hash.
    expected_sha = os.environ.get("VOICE_TYPER_ELECTRON_SHA256", "").strip().lower()
    if expected_sha:
        if len(expected_sha) != 64 or not all(c in "0123456789abcdef" for c in expected_sha):
            log.error(
                "[ELECTRON-BUILD] VOICE_TYPER_ELECTRON_SHA256 is set but is not a "
                "64-char hex SHA-256 (got %d chars) — refusing to launch the "
                "binary rather than guessing; unset the env var to skip the check",
                len(expected_sha),
            )
            return None
        try:
            import hashlib

            actual_sha = hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError as exc:
            log.warning(
                "[ELECTRON-BUILD] Failed to read %s for SHA-256 verification: %s "
                "— skipping integrity check (treat as untrusted)",
                candidate,
                exc,
            )
            return None
        if actual_sha != expected_sha:
            log.error(
                "[ELECTRON-BUILD] Electron binary CHECKSUM MISMATCH for %s — "
                "expected %s, got %s. Refusing to launch this binary; falling "
                "back to `npm run dev`. Unset VOICE_TYPER_ELECTRON_SHA256 to "
                "skip the check (XZ-R6-AS-02).",
                candidate,
                expected_sha,
                actual_sha,
            )
            return None
        log.debug(
            "[ELECTRON-BUILD] Electron binary checksum OK for %s (%s)",
            candidate.name,
            actual_sha,
        )
    return str(candidate)


def _main_entry_built() -> bool:
    """Return ``True`` if ALL compiled Electron bundles exist.

    ``electron .`` loads ``out/main/index.js`` (the electron-vite build
    output), the renderer via ``out/renderer/index.html``, and the
    preload from ``out/preload/index.js``.  All three must exist for the
    built app to display anything:

    * A missing renderer bundle makes the main window fail to load
      (``did-fail-load`` ERR_FILE_NOT_FOUND) — the window never shows
      and the process lingers as a hidden zombie holding the
      single-instance lock, silently killing every later launch
      (RACE-011).
    * A missing preload triggers ``preload-error`` → ``app.quit()``.

    If the client has never been fully built (fresh checkout, deleted
    ``out/``), this is ``False`` and the caller must run
    ``npm run build`` first — or fall back to ``npm run dev``, which
    builds-and-runs in one step.
    """
    return (
        (CLIENT_DIR / "out" / "main" / "index.js").exists()
        and (CLIENT_DIR / "out" / "renderer" / "index.html").exists()
        and (CLIENT_DIR / "out" / "preload" / "index.js").exists()
    )


def _npm_command(script: str = "dev") -> list[str] | None:
    """Return the command list to run ``npm run <script>``.

        Parameters
        ----------
        script : str
            npm script name, e.g. ``"dev"`` or ``"build"``.

        Returns
        -------
        list[str] | None
            The argv list to pass to :class:`subprocess.Popen`, or ``None``
            if npm truly cannot be resolved on the current platform.  When
            ``None`` is returned, the caller MUST log a clear error and skip
            the operation — it MUST NOT fall back to ``shell=True`` (S-7:
            shell=True is a shell-injection risk and breaks on paths with
            spaces).

    S-7: On Windows, npm is ``npm.cmd`` (a
        batch file).  Previously this returned ``None`` to signal "use
        ``shell=True``" which propagated PATH/env to a shell.  We now
        resolve the .cmd path directly via :func:`shutil.which` (which
        checks ``PATHEXT`` on Windows, so ``shutil.which("npm")`` already
        resolves to ``npm.cmd``), and as a belt-and-suspenders fallback on
        Windows we also try ``shutil.which("npm.cmd")`` explicitly in case
        ``PATHEXT`` is misconfigured.  The result is always a list form
        (no shell) when npm can be found, or ``None`` when it cannot —
        the caller logs and skips in the latter case.

        On POSIX, when ``shutil.which`` misses, we still return the list
        form ``["npm", "run", script]`` so :func:`subprocess.Popen` does
        the PATH lookup itself (functionally equivalent to the shell form
        but without spawning ``/bin/sh``).
    """
    import shutil

    npm_path = shutil.which("npm")
    if npm_path is not None:
        return [npm_path, "run", script]
    # Windows: ``shutil.which("npm")`` already consults PATHEXT and should
    # resolve to ``npm.cmd``.  As a defensive fallback for misconfigured
    # PATH/PATHEXT environments, try the ``.cmd`` extension explicitly.
    if is_windows():
        npm_cmd_path = shutil.which("npm.cmd")
        if npm_cmd_path is not None:
            return [npm_cmd_path, "run", script]
        # npm truly not resolvable — caller logs and skips (no shell=True).
        return None
    # POSIX: shutil.which missed, but Popen's PATH lookup may still find it.
    # Return the list form so Popen does the lookup without spawning a shell.
    return ["npm", "run", script]


def _spawn_flags(hidden: bool = False) -> dict:
    """Platform-specific kwargs for spawning the Electron child process.

    Parameters
    ----------
    hidden : bool
        If ``True`` (autostart at login), prevents console windows from
        flashing on Windows by adding ``CREATE_NO_WINDOW``.  If
        ``False`` (default — used by ``electron_launcher`` and by the
        autostart desktop-shortcut path), Windows child processes get
        normal process creation so they can create their own console
        windows if needed (e.g. for ``npm run dev``).

    On POSIX, the child is detached into a new session
    (``start_new_session=True``) so it survives the launcher process
    exiting — this is required for both the autostart path (the
    launcher exits immediately after spawning) and the standalone
    backend path (the backend may exit before Electron does).
    """
    kwargs: dict = {}
    if is_windows():
        if hidden:
            # CREATE_NO_WINDOW (0x08000000) prevents a console from
            # flashing during autostart (the user is logging in, not
            # clicking a shortcut).
            kwargs["creationflags"] = 0x08000000
        # else: no creation flags — processes get normal console
        # behavior, which lets `npm run dev` open its own console.
    else:
        # Detach into a new session so the child survives this launcher.
        kwargs["start_new_session"] = True
    return kwargs


def _launcher_child_env() -> dict[str, str]:
    """Build the base env dict for Electron/Tauri children whose output is redirected to log files.

    The child's stdout/stderr land in ``electron-stdout.log`` /
    ``electron-stderr.log`` (or the ``tauri-stdout.log`` /
    ``tauri-stderr.log`` equivalents — the same helper is used by
    ``autostart_launcher._spawn_tauri_host`` and the Tauri focus-probe
    branch of ``_focus_running_app``). These tweaks keep those files
    clean (matching the plain-text format of ``voice-typer.log``):

    - ``FORCE_COLOR=0`` — some JS tooling (vite/rollup/chalk) force-
      enables ANSI colour even when stdout is NOT a TTY; this disables
      it so no escape codes reach the log file.
    - ``NO_COLOR=1`` — the de-facto cross-ecosystem no-ANSI contract
      (no-color.org) honoured by Rust console crates / CLI tooling that
      ignores ``FORCE_COLOR`` (the Tauri host is a Rust binary). Belt-
      and-suspenders for anything the host or its tooling prints to
      stderr; the Rust logger itself is ANSI-free, this guards the
      rest.
    - ``CLICOLOR=0`` — the BSD/macOS convention for tools that honour
      ``CLICOLOR`` instead of ``NO_COLOR`` (Cargo/rustc-side tooling on
      macOS, `xcodebuild`, etc.).
    - ``RUST_LOG_STDERR=0`` — the Rust host's ``CombinedLogger``
      mirrors its entire rotating-file stream (``voice-typer-rust.log``)
      to stderr when ``RUST_LOG_STDERR=1`` is inherited. That flag
      exists for terminal tailing (``journalctl``/`cargo tauri dev`);
      when stderr is redirected to ``tauri-stderr.log`` it would just
      DUPLICATE the file stream. Force it off so ``tauri-stderr.log``
      stays clean and carries only crash/early diagnostics (the panic
      hook + ``EarlyLogger`` write to stderr directly and are NOT
      gated by this var).
    - ``npm_config_loglevel=silent`` — suppress npm's banner notices
      (``npm notice run voice-typer-desktop@1.0.0 dev``) written by the
      npm parent process.

    Callers apply their own overrides on top (``VT_START_HIDDEN`` /
    ``VT_FOCUS_ONLY`` / IPC token env vars / the Electron launcher's
    sensitive-env stripping). A terminal run of ``npm run dev`` (or a
    direct ``cargo tauri dev``) does NOT go through this helper, so
    interactive sessions keep colours, npm notices, and the
    ``RUST_LOG_STDERR`` escape hatch.
    """
    env = dict(os.environ)
    env["FORCE_COLOR"] = "0"
    env["NO_COLOR"] = "1"
    env["CLICOLOR"] = "0"
    env["RUST_LOG_STDERR"] = "0"
    env["npm_config_loglevel"] = "silent"
    return env


def _electron_log_files() -> dict:
    """Return DEVNULL for Electron's stdout/stderr (O4: no duplicate capture).

    The Electron app's own loggers (``structuredLogger`` → ``electron-main.log``,
    ``printfLogger`` → ``electron-runtime.log``) already capture all warnings,
    errors, and lifecycle events.  Raw child stdout/stderr capture
    (``electron-stdout.log`` / ``electron-stderr.log``) duplicated those
    same lines because ``log.warn``/``log.error`` writes to both
    ``console.error/warn`` (raw pipe) AND the file tee (``appendLogLine``).

    RACE-009 originally added this raw capture so Electron crashes could
    be diagnosed — that value is now served by:
    ``electron-crashes.log`` (uncaughtException handler),
    ``electron-main.log``/``electron-runtime.log`` (structured logging),
    and the VEH crash buffer.
    """

    return {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


# substring markers for "sensitive" env var names. When a child process
# inherits the parent's env (intentional — same-app restart), we log
# ONLY the key names matching one of these markers so a future leak in a
# downstream log is auditable. Values are NEVER printed. The list is
# intentionally conservative — it catches the common SaaS API-key
# conventions (OPENAI_API_KEY, ANTHROPIC_API_KEY, HF_TOKEN,
# GEMINI_API_KEY, AZURE_SPEECH_KEY, etc.) and OS-level secrets
# (AWS_SECRET_ACCESS_KEY, *_PASSWORD) without flagging benign vars
# (PATH, HOME, LANG, VT_PYTHON_PORT, etc.).
_SENSITIVE_ENV_MARKERS = (
    "_API_KEY",
    "_SECRET",
    "_TOKEN",
    "_PASSWORD",
    "_CREDENTIAL",
    "AWS_SECRET_ACCESS_KEY",
)


def _redact_sensitive_env_keys(env: dict[str, str]) -> list[str]:
    """Return the NAMES of env keys that look sensitive.

    Helper used by the Electron / autostart launchers right after
    ``env = dict(os.environ)`` to surface (without values) which
    sensitive-looking env vars the child will inherit. The list is
    intended for an audit log line — it is NOT a security control.
    """
    return sorted(key for key in env if any(marker in key.upper() for marker in _SENSITIVE_ENV_MARKERS))


def _log_sensitive_env_keys(env: dict[str, str], *, context: str) -> None:
    """Log (at INFO) the names of sensitive env keys present in ``env``.

    Only the KEY NAMES are logged — values are never printed. If no
    sensitive keys are present, nothing is logged (avoids log noise on
    the common case).
    """
    sensitive = _redact_sensitive_env_keys(env)
    if sensitive:
        log.info(
            "[ENV] %s: child inherits sensitive env keys (names only, values redacted): %s",
            context,
            ", ".join(sensitive),
        )
