#!/usr/bin/env python3
"""Write CI-errors.md from JUnit XML test results.

Parses one or more JUnit XML files (one per CI matrix leg, e.g.
``junit/pytest-results-<os>-<py>/pytest-results.xml``) and writes a
markdown summary of every failing/errored test to CI-errors.md at the
repo root. Each entry carries the test identity (class + name), the
matrix legs it failed on, the source location parsed out of the
traceback (``path:line``), and the error itself. If no failures are
found, CI-errors.md records the green state so readers know the CI
passed without needing GitHub CLI access.

Robustness notes (each learned from a real silent-miss):

* Illegal XML 1.0 characters (raw control bytes a test may print
  into captured output) are stripped BEFORE parsing — otherwise one
  bad byte in one leg's file fails the whole parse and hides every
  failure in that file.
* Truncated files (a leg killed mid-write still uploads via
  ``if: always()``) fall back to regex extraction of the
  ``<testcase>`` blocks that ARE complete, so partial data still
  surfaces instead of a single "unparseable" pseudo-entry.
* Every input file is parsed independently — one corrupt file can
  never hide another leg's failures.

Usage:
    python scripts/ci/write_ci_errors.py [junit_xml...]

Exit code is always 0 — the script is documentation, not a gate. The
workflow step that calls it runs `if: always()` so the report is
written even when the tests themselves fail.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[2] / "CI-errors.md"

# Characters illegal in XML 1.0 (https://www.w3.org/TR/xml/#charsets).
# Everything outside these ranges must go before handing text to a
# non-recovering parser like ElementTree.
_ILLEGAL_XML_RE = re.compile("[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

# A traceback frame line, e.g.:
#   File "/home/runner/work/voice-typer/voice-typer/tests/test_x.py", line 123, in test_y
#   File "C:\...\tests\test_x.py", line 123, in test_y
#   E   AssertionError: boom
_FRAME_RE = re.compile(r'^\s*(?:E\s+)?File "([^"]+)", line (\d+)', re.MULTILINE)

# pytest's short traceback summary lines, e.g.:
#   tests/handlers/test_x.py:39: in <module>
#   voice_typer/server/y.py:12: ValueError
_SHORT_FRAME_RE = re.compile(r"^([\w\-.\\/]+\.py):(\d+):", re.MULTILINE)

# pytest's internal-error pseudo-test (xdist collection crash, ...).
# It has no repo location — surface the underlying error instead.
_INTERNAL_TEST = ("pytest", "internal")

# Cap per-entry body so one giant traceback can't bloat the doc.
_MAX_BODY_LINES = 40
_MAX_BODY_CHARS = 4000


def _sanitize(raw: bytes) -> str:
    """Decode leniently and strip XML-1.0-illegal characters."""
    text = raw.decode("utf-8", errors="replace")
    return _ILLEGAL_XML_RE.sub("\ufffd", text)


def _leg_of(xml_path: Path) -> str:
    """Matrix-leg label from the artifact layout, e.g.
    ``junit/pytest-results-windows-2022-3.13/pytest-results.xml`` →
    ``windows-2022-3.13``. Falls back to the file stem."""
    parent = xml_path.parent.name
    for prefix in ("pytest-results-",):
        if parent.startswith(prefix):
            return parent[len(prefix) :]
    if xml_path.name != "pytest-results.xml":
        return xml_path.stem
    return "unknown-leg"


def _location_and_error(text: str) -> tuple[str, str, str]:
    """Split a failure/error body into (location, error_line, body).

    *location* is the deepest traceback frame inside the repo
    (``tests/`` or ``voice_typer/``), rendered ``path:line`` with any
    runner-specific prefix stripped. *error_line* is the trailing
    exception line (``E   AssertionError: ...``). Falls back to
    ``(unknown location)`` when no repo frame exists (e.g. xdist
    internal errors).
    """
    location = "(unknown location)"
    for match in _FRAME_RE.finditer(text):
        path, lineno = match.group(1), match.group(2)
        norm = path.replace("\\", "/")
        repo_idx = norm.find("voice-typer/")
        short = norm[repo_idx + len("voice-typer/") :] if repo_idx != -1 else norm
        if short.startswith(("tests/", "voice_typer/")):
            location = f"{short}:{lineno}"
    if location == "(unknown location)":
        # Fall back to pytest's short summary lines (collection
        # errors print `path:line: in ...` instead of File frames).
        for match in _SHORT_FRAME_RE.finditer(text):
            path, lineno = match.group(1), match.group(2)
            norm = path.replace("\\", "/")
            if norm.startswith(("tests/", "voice_typer/")):
                location = f"{norm}:{lineno}"
                break
    error_line = "(no error line)"
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("E ") and len(stripped) > 2:
            error_line = stripped[2:].strip()
            break
    return location, error_line, text.strip()


def _from_element_tree(root: ET.Element, leg: str) -> list[tuple[str, str, str, str, str]]:
    """Extract (class, test, leg, location, body) via ElementTree."""
    out = []
    for suite in root.iter("testsuite"):
        for case in suite.iter("testcase"):
            classname = case.get("classname", "<unknown>")
            test_name = case.get("name", "<unknown>")
            node = case.find("failure")
            if node is None:
                node = case.find("error")
            if node is None:
                continue
            message = (node.get("message") or "").strip()
            text = (node.text or "").strip()
            body = (message + "\n" + text).strip() if message else text
            location, error_line, _ = _location_and_error(body or text)
            if not body:
                body = "(no message)"
            if (classname, test_name) == _INTERNAL_TEST:
                location = "(pytest internal error — no test location)"
                error_line = message or error_line
            out.append((classname, test_name, leg, location, f"{error_line}\n\n{body}"))
    return out


def _from_regex_fallback(text: str, leg: str) -> list[tuple[str, str, str, str, str]]:
    """Best-effort extraction from a truncated/malformed file.

    Finds complete ``<testcase ...>...<failure|error...>...</>...</testcase>``
    spans with regexes. Incomplete trailing spans are skipped —
    whatever parsed, surfaces.
    """
    out = []
    case_re = re.compile(
        r"<testcase\b(?P<attrs>[^>]*?)>"
        r"(?P<inner>.*?)"
        r"</testcase\s*>",
        re.DOTALL,
    )
    attr_re = re.compile(r'''(classname|name)="([^"]*)"''')
    fail_re = re.compile(
        r"<(?:failure|error)\b[^>]*>(?P<body>.*?)</(?:failure|error)\s*>",
        re.DOTALL,
    )
    tag_re = re.compile(r"<[^>]+>")
    for case in case_re.finditer(text):
        attrs = dict(attr_re.findall(case.group("attrs")))
        fail = fail_re.search(case.group("inner"))
        if fail is None:
            continue
        body = tag_re.sub("", fail.group("body")).strip()
        body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        location, error_line, _ = _location_and_error(body)
        classname = attrs.get("classname", "<unknown>")
        test_name = attrs.get("name", "<unknown>")
        if not body:
            body = "(no message)"
        out.append((classname, test_name, leg, location, f"{error_line}\n\n{body}"))
    return out


def _extract_failures(xml_path: Path) -> tuple[list[tuple[str, str, str, str, str]], str]:
    """Return (failures, note). *note* is non-empty when degraded."""
    leg = _leg_of(xml_path)
    try:
        raw = xml_path.read_bytes()
    except FileNotFoundError:
        return [], f"{xml_path}: file not found, skipped"
    text = _sanitize(raw)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        recovered = _from_regex_fallback(text, leg)
        note = (
            f"{xml_path.name} ({leg}): XML would not parse — "
            f"recovered {len(recovered)} complete testcase(s) by fallback"
        )
        return recovered, note
    return _from_element_tree(root, leg), ""


def _trim_body(body: str) -> str:
    lines = body.splitlines()[:_MAX_BODY_LINES]
    trimmed = "\n".join(lines).strip()
    if len(trimmed) > _MAX_BODY_CHARS:
        trimmed = trimmed[:_MAX_BODY_CHARS].rstrip() + "\n… (truncated)"
    return trimmed or "(no message)"


def main(argv: list[str]) -> int:
    # Group by (class, test): one entry per failing test, legs listed.
    grouped: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    notes: list[str] = []
    files_checked = 0
    for raw in argv:
        path = Path(raw)
        if path.is_dir():
            continue
        files_checked += 1
        failures, note = _extract_failures(path)
        if note:
            notes.append(note)
        for classname, test_name, leg, location, body in failures:
            key = (classname, test_name)
            entry = grouped.get(key)
            if entry is None:
                entry = {"legs": [], "location": location, "body": body}
                grouped[key] = entry
                order.append(key)
            if leg not in entry["legs"]:
                entry["legs"].append(leg)
            if entry["location"] == "(unknown location)" and location != "(unknown location)":
                entry["location"] = location
                entry["body"] = body

    if not grouped:
        lines = [
            "# CI Errors",
            "",
            "> Auto-generated from the latest GitHub Actions run via "
            "`scripts/ci/write_ci_errors.py`. Do not edit by hand — it is "
            "overwritten on every CI run.",
            "",
            f"No test failures in the latest CI run. ✅ ({files_checked} JUnit file(s) checked)",
            "",
        ]
        if notes:
            lines += ["Degraded inputs (no failures lost — files were empty):", ""]
            lines += [f"- {note}" for note in notes] + [""]
        OUTPUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print(f"CI-errors.md: no failures ({files_checked} junit file(s) checked)")
        return 0

    total_legs: set[str] = set()
    for key in order:
        total_legs.update(grouped[key]["legs"])
    lines = [
        "# CI Errors",
        "",
        "> Auto-generated from the latest GitHub Actions run via "
        "`scripts/ci/write_ci_errors.py`. Do not edit by hand — it is "
        "overwritten on every CI run.",
        "",
        f"**{len(order)} failing/errored test(s)** across {len(total_legs)} matrix leg(s).",
        "",
    ]
    if notes:
        lines += ["Degraded inputs (recovered by fallback — see notes):", ""]
        lines += [f"- {note}" for note in notes] + [""]
    for i, key in enumerate(order, 1):
        entry = grouped[key]
        classname, test_name = key
        # Collection errors carry an empty classname and the module
        # path as the test name — render without a leading dot.
        title = f"{classname}.{test_name}" if classname.strip() else test_name
        legs = ", ".join(sorted(entry["legs"]))
        lines += [
            f"### {i}. `{title}`",
            "",
            f"- Legs: {legs}",
            f"- Location: `{entry['location']}`",
            "",
            "```",
            _trim_body(entry["body"]),
            "```",
            "",
        ]

    OUTPUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"CI-errors.md: wrote {len(order)} failure(s) from {files_checked} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
