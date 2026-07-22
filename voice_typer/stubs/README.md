# voice_typer/stubs/ — pyrefly type stubs for platform-only deps

## Purpose

These `.pyi` stub files exist so that `pyrefly check voice_typer/` stops
reporting `missing-import` / `missing-attribute` on third-party modules
that are **never installed on the CI runner's platform**:

| Module                | Pip package                       | Platform  | Used by                                  |
| --------------------- | --------------------------------- | --------- | ---------------------------------------- |
| `pycaw.pycaw`         | `pycaw`                           | Windows   | `server/volume_backends.py` (WinVolume)  |
| `comtypes`            | `comtypes`                        | Windows   | `server/volume_backends.py`, `clipboard.py` (UIA) |
| `comtypes.client`     | `comtypes`                        | Windows   | `server/clipboard.py` (UIA)              |
| `CoreAudio`           | `pyobjc-framework-CoreAudio`      | macOS     | `server/volume_backends.py` (MacVolume)  |
| `CoreFoundation`      | `pyobjc-framework-CoreFoundation` | macOS     | `server/permissions.py` (AX check)       |
| `ApplicationServices` | `pyobjc-framework-ApplicationServices` | macOS | `server/permissions.py` (AX check)       |
| `Foundation`          | `pyobjc-framework-Cocoa`          | macOS     | tray (pystray backend)                   |
| `AppKit`              | `pyobjc-framework-Cocoa`          | macOS     | tray (pystray backend)                   |
| `Cocoa`               | `pyobjc-framework-Cocoa`          | macOS     | tray (pystray backend)                   |
| `objc`                | `pyobjc-core`                     | macOS     | pyobjc runtime (rarely direct import)    |
| `winreg`              | (stdlib, Windows-only)            | Windows   | `server/server_platform.py`, `task_scheduler.py` |

All stubs use `Any` types because the actual implementations are
lazy-imported inside `try / except ImportError` blocks — pyrefly only
needs to know the import surface so it can follow the *real* code paths
underneath, not verify the platform-only call sites.

## Naming convention

Stub file names match the **module import name** (e.g. `CoreAudio.pyi`),
not the pip distribution name (e.g. `pyobjc-framework-CoreAudio`). This
is required by pyrefly's `search-path` resolver, which looks up
`<search-path>/<module-name>.pyi` (or `<search-path>/<module-name>/__init__.pyi`
for packages).

The original task spec used distribution-style names (`pyobjc_core.pyi`,
`pyobjc_CoreAudio.pyi`, `pyobjc_Cocoa.pyi`) — those are aliases for the
correctly-named `objc.pyi`, `CoreAudio.pyi`, `Cocoa.pyi` shipped here.

## Configuration

`pyproject.toml` → `[tool.pyrefly]`:

```toml
search-path = ["voice_typer/stubs"]
```

## Adding a new platform-only dep

1. Grep the codebase for the actual import surface:
   ```bash
   grep -rn "from <module>\|import <module>" voice_typer/
   ```
2. Add a `.pyi` file declaring exactly those names with `Any` types.
3. Re-run `pyrefly check voice_typer/` to confirm the `missing-import`
   count drops.
4. Do NOT add `# type: ignore` to the call sites — the stub replaces
   the need for it.
