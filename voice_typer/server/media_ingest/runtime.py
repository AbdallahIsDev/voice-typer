"""JavaScript runtime discovery for YouTube extraction (ADR-0023).

YouTube challenges require a complete external JavaScript runtime. This
module finds a runtime without downloading or installing anything. The
offline pack is the Deno boundary (ADR-0023 packaging): a pack-shipped
Deno at ``<pack-root>/<version>/bin/deno[.exe]`` is probed FIRST, then
PATH ``deno``, then PATH ``node`` >= 20.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from voice_typer.server.platform_utils import is_windows


@dataclass(frozen=True)
class JsRuntime:
    """Usable external JavaScript runtime executable."""

    name: str
    path: str


def find_js_runtime(
    *,
    path: str | None = None,
    env: dict[str, str] | None = None,
    which: Callable[..., str | None] | None = None,
    pack_root: Path | str | None = None,
) -> JsRuntime | None:
    """Return the pack-shipped Deno, else PATH Deno, else Node 20+, else None."""
    packed = _pack_deno_path(pack_root)
    if packed is not None:
        return JsRuntime(name="deno", path=str(packed))
    search = which or shutil.which
    deno = search("deno", path=path) if path else search("deno")
    if deno:
        return JsRuntime(name="deno", path=deno)
    node = search("node", path=path) if path else search("node")
    if node and _node_ok(node, env=env):
        return JsRuntime(name="node", path=node)
    return None


def _pack_deno_path(pack_root: Path | str | None) -> Path | None:
    """Locate the offline-pack Deno binary, or None (best-effort, no raise)."""
    exe = "deno.exe" if is_windows() else "deno"
    root = Path(pack_root) if pack_root is not None else None
    try:
        from voice_typer.server.service import update_check
        from voice_typer.server.service.offline_pack import offline_pack_dir_for_version

        version = update_check._local_offline_pack_version(root=root)
        if not version:
            return None
        candidate = offline_pack_dir_for_version(version, root=root) / "bin" / exe
    except Exception:  # noqa: BLE001, runtime discovery never blocks ingest
        return None
    return candidate if candidate.is_file() else None


def js_runtime_options(runtime: JsRuntime | None) -> dict[str, str] | None:
    """Return yt-dlp ``js_runtimes`` options for a discovered runtime."""
    if runtime is None:
        return None
    if runtime.name == "deno":
        return {"deno": runtime.path}
    if runtime.name == "node":
        return {"node": runtime.path}
    return None


def _node_ok(node_path: str, *, env: dict[str, str] | None = None) -> bool:
    import subprocess

    try:
        completed = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=env or os.environ,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    version = (completed.stdout or "").strip().lstrip("vV")
    try:
        major = int(version.split(".", 1)[0])
    except ValueError:
        return False
    return major >= 20
