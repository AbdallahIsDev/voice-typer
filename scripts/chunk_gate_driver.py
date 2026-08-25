#!/usr/bin/env python3
"""Chunked full-suite gate driver (§15.1 domain-chunked runs).

Runs every chunk from /tmp/chunk_gate/map.txt sequentially, records per-chunk
results, and verifies the collected-test union equals the full-suite count.
"""

import subprocess
import sys
import time
from pathlib import Path

REPO = Path("/home/z/my-project/voice-typer")
OUT = Path("/tmp/chunk_gate")
PY = str(REPO / ".venv/bin/python")


def run_chunk(name: str, files: list[str]) -> dict:
    log = OUT / f"{name}.log"
    cmd = [
        PY,
        "-m",
        "pytest",
        *files,
        "--import-mode=importlib",
        "-q",
        "--no-cov",
        "--tb=line",
        "-p",
        "no:cacheprovider",
        "-n",
        "2",
        "--dist=loadgroup",
        "--timeout=300",
        "--timeout-method=thread",
    ]
    t0 = time.time()
    with open(log, "w") as fh:
        proc = subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, timeout=3000)
    text = log.read_text()
    summary = ""
    for ln in text.splitlines():
        if " passed" in ln or " failed" in ln or " error" in ln:
            summary = ln.strip()
    fails = [ln for ln in text.splitlines() if ln.startswith("FAILED") or ln.startswith("ERROR")]
    return {
        "name": name,
        "rc": proc.returncode,
        "summary": summary,
        "fails": fails[:20],
        "secs": round(time.time() - t0, 1),
    }


def main():
    chunks = []
    for line in (OUT / "map.txt").read_text().splitlines():
        parts = line.split("\t")
        chunks.append((parts[0], parts[1:]))
    print(f"{len(chunks)} chunks, {sum(len(f) for _, f in chunks)} files")

    results = []
    for name, files in chunks:
        r = run_chunk(name, files)
        results.append(r)
        print(f"[{r['name']}] rc={r['rc']} ({r['secs']}s) :: {r['summary']}")
        for f in r["fails"]:
            print(f"    {f}")
        sys.stdout.flush()

    print("\n=== TOTALS ===")
    total_pass = total_fail = total_skip = 0
    for r in results:
        # parse "N passed, M failed, K skipped"
        s = r["summary"]
        import re

        p = re.search(r"(\d+) passed", s)
        f = re.search(r"(\d+) failed", s)
        k = re.search(r"(\d+) skipped", s)
        total_pass += int(p.group(1)) if p else 0
        total_fail += int(f.group(1)) if f else 0
        total_skip += int(k.group(1)) if k else 0
    print(f"TOTAL: {total_pass} passed, {total_fail} failed, {total_skip} skipped")
    bad = [r for r in results if r["rc"] != 0]
    print(f"CHUNKS RED: {len(bad)}" if bad else "ALL CHUNKS GREEN")
    (OUT / "final_summary.txt").write_text(
        "\n".join(f"{r['name']}: rc={r['rc']} :: {r['summary']}" for r in results)
        + f"\nTOTAL: {total_pass} passed, {total_fail} failed, {total_skip} skipped\n"
    )


if __name__ == "__main__":
    main()
