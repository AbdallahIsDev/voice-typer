"""Task Scheduler XML helpers for the Windows autostart mechanisms."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _extract_command_from_task_xml(xml_str: str) -> str | None:
    """Extract the ``<Command>`` element's text from a Task Scheduler XML.

    Returns the command path as a string, or ``None`` if the XML is
    """
    if not xml_str:
        return None
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_str)
        # Search for any element with local name "Command" (the Task
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
    """Extract the ``<Arguments>`` element's text from a Task Scheduler XML."""
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
