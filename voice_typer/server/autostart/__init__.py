"""Implementation subpackage for the universal autostart launcher.

``voice_typer/server/autostart_launcher.py`` is the OS-facing entry
script (its path is embedded in registry Run keys, LaunchAgents,
``.desktop`` files, and shortcuts); the actual spawn / probe / PID
logic lives in these leaf modules:

- :mod:`voice_typer.server.autostart.log_files`   -- child stdout/stderr handles
- :mod:`voice_typer.server.autostart.pid_file`    -- PID/port file helpers
- :mod:`voice_typer.server.autostart.port_probe`  -- backend readiness polling
- :mod:`voice_typer.server.autostart.tauri_spawn` -- Tauri binary discovery/integrity/spawn
- :mod:`voice_typer.server.autostart.electron_spawn` -- built Electron / npm-run-dev spawns
- :mod:`voice_typer.server.autostart.focus`       -- focus-running-instance probe

Patch-target contract: tests (and any future caller) monkeypatch these
helpers on the facade (``voice_typer.server.autostart_launcher.X``);
the leaf modules resolve cross-helper references through the facade at
call time so a facade rebinding is visible everywhere, exactly as when
the code lived in one module namespace.
"""
