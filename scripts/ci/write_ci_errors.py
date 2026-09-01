#!/usr/bin/env python3
"""Write CI-errors.md from JUnit XML test results.

Parses one or more JUnit XML files (pytest-results.xml, vitest junit output,
etc.) and writes a concise markdown summary of every failing/errored test to
CI-errors.md at the repo root. If no failures are found, CI-errors.md is
written with a "no failures" message so the cloud agent can read it and know
the CI is green without needing GitHub CLI access.

Usage:
    python scripts/ci/write_ci_errors.py [junit_xml...]

Exit code is always 0 — the script is documentation, not a gate. The
workflow step that calls it runs `if: always()` so the report is written
even when the tests themselves fail.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[2] / "CI-errors.md"


def _extract_failures(xml_path: Path) -> list[tuple[str, str, str]]:
    """Return (suite_name, test_name, message) for every failed/errored case."""
    out: list[tuple[str, str, str]] = []
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        return [(xml_path.name, "<unparseable-xml>", str(exc))]
    except FileNotFoundError:
        return []
    root = tree.getroot()
    for suite in root.iter("testsuite"):
        suite_name = suite.get("name", xml_path.name)
        for case in suite.iter("testcase"):
            test_name = case.get("name", "<unknown>")
            for kind in ("failure", "error"):
                node = case.find(kind)
                if node is not None:
                    message = (node.get("message") or "").strip()
                    text = (node.text or "").strip()
                    detail = message or (text.splitlines()[0] if text else "") or "(no message)"
                    out.append((suite_name, test_name, detail))
    return out


def main(argv: list[str]) -> int:
    failures: list[tuple[str, str, str]] = []
    for raw in argv:
        failures.extend(_extract_failures(Path(raw)))

    # Deduplicate by (suite, test) keeping the first message.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str, str]] = []
    for suite, test, msg in failures:
        key = (suite, test)
        if key not in seen:
            seen.add(key)
            unique.append((suite, test, msg))

    if not unique:
        OUTPUT.write_text(
            "# CI Errors\n\nNo test failures in the latest CI run. ✅\n",
            encoding="utf-8",
        )
        print(f"CI-errors.md: no failures ({len(argv)} junit file(s) checked)")
        return 0

    lines = [
        "# CI Errors",
        "",
        "> Auto-generated from the latest GitHub Actions run via "
        "`scripts/ci/write_ci_errors.py`. Do not edit by hand — it is "
        "overwritten on every CI run. If this file is empty, all tests passed.",
        "",
        f"**{len(unique)} failing/errored test(s).**",
        "",
    ]
    for i, (suite, test, msg) in enumerate(unique, 1):
        lines += [
            f"### {i}. `{suite}.{test}`",
            "",
            "```",
            msg[:2000],
            "```",
            "",
        ]

    OUTPUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"CI-errors.md: wrote {len(unique)} failure(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
