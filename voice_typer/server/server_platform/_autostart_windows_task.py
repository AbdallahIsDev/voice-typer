"""Task Scheduler XML helpers for the Windows autostart mechanisms.

Extracted from ``voice_typer/server/server_platform/autostart_windows.py``
(the orchestrating facade for the three Windows autostart mechanisms —
Task Scheduler, Startup-folder .bat, HKCU Run key). This module owns the
PURE task-XML parsers: they take a Task Scheduler XML string and return
the ``<Command>`` / ``<Arguments>`` element text. No registry, subprocess,
or platform side effects — safe on every platform.

Patch contract: tests reach these functions through the facade module
(``voice_typer.server.server_platform.autostart_windows``), which
re-imports them at module level; facade callers resolve them through
plain module-global lookups at call time, so facade patches are seen.
The legacy-task sweep (``_autostart_windows_sweep``) reads them through
the facade module object at call time for the same reason.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _extract_command_from_task_xml(xml_str: str) -> str | None:
    """Extract the ``<Command>`` element's text from a Task Scheduler XML.

    Returns the command path as a string, or ``None`` if the XML is
    malformed or has no ``<Command>`` element. Used by
    :func:`autostart_windows._is_app_autostart_task_registered` to
    validate that the task's command path actually exists on disk
    (stale-task detection).

    The Task Scheduler XML uses the namespace
    ``http://schemas.microsoft.com/windows/2004/02/mit/task``. We
    search for any element whose local name is ``Command`` (ignoring
    the namespace) so the parse is robust to namespace prefix changes.
    """
    if not xml_str:
        return None
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        # Search for any element with local name "Command" (the Task
        # Scheduler XML places it under Actions/Exec/Command, but we
        # search recursively to be robust to schema variations).
        for elem in root.iter():
            tag = elem.tag
            # Strip namespace prefix if present (e.g. "{ns}Command").
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            if tag == "Command" and elem.text:
                return elem.text.strip()
    except Exception:
        log.debug("[AUTOSTART] _extract_command_from_task_xml parse failed", exc_info=True)
    return None


def _extract_arguments_from_task_xml(xml_str: str) -> str | None:
    """Extract the ``<Arguments>`` element's text from a Task Scheduler XML.

    Companion to :func:`_extract_command_from_task_xml` — used by the
    legacy-entry sweep (:func:`autostart_windows._sweep_legacy_tasks`,
    in :mod:`._autostart_windows_sweep`) to inspect the task's
    command-line arguments (which embed the per-install
    ``autostart_launcher.py`` path). Returns the arguments string, or
    ``None`` if the XML is malformed or has no ``<Arguments>`` element.
    """
    if not xml_str:
        return None
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        for elem in root.iter():
            tag = elem.tag
            # Strip namespace prefix if present (e.g. "{ns}Arguments").
            if "}" in tag:
                tag = tag.split("}", 1)[1]
            if tag == "Arguments" and elem.text:
                return elem.text.strip()
    except Exception:
        log.debug("[AUTOSTART] _extract_arguments_from_task_xml parse failed", exc_info=True)
    return None
