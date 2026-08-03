"""Re-export shim for the resource probe.

The probe body was extracted into ``voice_typer/server/resource_probe.py``
in an earlier refactor (it was a 185-LOC self-contained probe with no
instance-state dependencies). This module re-exports the two public
entry points so callers within the package can import them via a
stable path:

    from voice_typer.server.dictation_pipeline.resource_probe import (
        check_resources,
        check_resources_throttled,
    )

Both functions are also reachable directly from
``voice_typer.server.resource_probe``; the duplicate path exists so
the package's surface is self-contained (the split plan lists
``dictation_pipeline/resource_probe.py`` as a sibling helper even
though the body lives one directory up).

CONSTRAINTS.md C-DATA-1: the probe performs NO network calls — it only
reads local system state via ``psutil.virtual_memory`` /
``shutil.disk_usage`` / ``os.statvfs`` / ``ctypes.windll.kernel32.GlobalMemoryStatusEx``
/ ``torch.cuda.memory_*``.
"""

from __future__ import annotations

from voice_typer.server.resource_probe import (  # noqa: F401
    check_resources,
    check_resources_throttled,
)

__all__ = ["check_resources", "check_resources_throttled"]
