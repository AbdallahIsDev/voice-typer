"""Scripts/generate_checksums.py — compute SHA-256 checksums for release artifacts.

TEST-037: Generates SHA256SUMS.txt from release artifacts.
Usage:
    python scripts/generate_checksums.py [dist_dir]

If no directory is specified, defaults to dist/.
"""

import hashlib
import sys
from pathlib import Path


def compute_sha256(filepath: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_checksums(dist_dir: Path) -> None:
    """Generate SHA256SUMS.txt for all release artifacts in dist_dir."""
    if not dist_dir.exists():
        print(f"Error: {dist_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    # Find all release artifacts (exe, zip, dmg, AppImage, deb, etc.)
    extensions = {".exe", ".zip", ".dmg", ".AppImage", ".deb", ".rpm", ".tar.gz", ".whl"}
    artifacts = []
    for f in sorted(dist_dir.rglob("*")):
        if f.is_file():
            # Check if the file has a known release extension
            suffix = "".join(f.suffixes)  # handles .tar.gz
            if any(suffix.endswith(ext) for ext in extensions) or f.name == "SHA256SUMS.txt":
                continue
            if f.name == "SHA256SUMS.txt":
                continue
            artifacts.append(f)

    if not artifacts:
        print(f"No release artifacts found in {dist_dir}", file=sys.stderr)
        sys.exit(1)

    output_path = dist_dir / "SHA256SUMS.txt"
    lines = []
    for artifact in artifacts:
        sha256 = compute_sha256(artifact)
        # Relative path from dist_dir
        rel_path = artifact.relative_to(dist_dir)
        line = f"{sha256}  {rel_path}"
        lines.append(line)
        print(f"  {sha256}  {rel_path}")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {len(lines)} checksums to {output_path}")


if __name__ == "__main__":
    dist_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    generate_checksums(dist_dir)
